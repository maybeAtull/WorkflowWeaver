"""Live end-to-end check THROUGH THE API: a real server, real HTTP and SSE, real services.

Run from the backend/ folder:  python -m scripts.smoke_api --live

THIS WRITES TO REAL SERVICES, which is why it refuses to run without --live. It starts the real
server (uvicorn) on a free local port and talks to it only over HTTP, the way a browser does,
then runs three templates for real:

  1. meeting_to_tasks   no injection      -> completed (Notion page, Drive file, one chat message)
  2. sprint_planning    s2:fatal          -> rolled_back (the Notion page is trashed, NO chat message)
  3. incident_response  s2:transient      -> completed after a visible retry (one chat message)

Runs 1 and 3 each leave a Notion page, a Drive file and a chat message behind (printed at the end
so they can be deleted). Between runs it waits about a minute, because Groq's free tier allows
roughly one workflow per minute. Prints the Langfuse trace links to open and check by eye.

Never prints a key. The server's own output is printed only if something fails. Exit code 0 only
if every check passed.
"""

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
STARTUP_TIMEOUT_SECONDS = 40
# The slowest honest run: the LLM twice, three tools, and the first Drive start (about 10 s).
RUN_TIMEOUT_SECONDS = 240
WAIT_BETWEEN_RUNS_SECONDS = 65
STORE_SETTLE_SECONDS = 15

Result = tuple[str, bool, str]


@dataclass(frozen=True)
class Plan:
    """One run to make, and what must hold afterwards."""

    template_id: str
    simulate: str | None
    status: str
    must_see: frozenset[str] = frozenset()  # event types that have to appear
    announces: bool = True  # whether a chat message must have been sent


PLANS = [
    Plan("meeting_to_tasks", None, "completed"),
    Plan(
        "sprint_planning",
        "s2:fatal",
        "rolled_back",
        frozenset({"step_failed", "rollback_started", "rollback_step"}),
        announces=False,
    ),
    Plan("incident_response", "s2:transient", "completed", frozenset({"step_retry"})),
]


# --------------------------------------------------------------------------- SSE parsing


def parse_sse(lines: Iterable[str]) -> list[dict[str, str]]:
    """Group SSE lines into messages {event, id, data}. A blank line ends a message and
    a line starting with ':' is a keepalive comment."""
    messages: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in lines:
        if not line:
            if current:
                messages.append(current)
                current = {}
        elif not line.startswith(":"):
            name, _, value = line.partition(": ")
            current[name] = value
    if current:
        messages.append(current)
    return messages


def decode(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The JSON events inside the messages."""
    return [json.loads(m["data"]) for m in messages if "data" in m]


# --------------------------------------------------------------------------- the checks
# Each takes plain data and returns (name, ok, detail) tuples, so they are testable offline.


def check_stream(
    messages: list[dict[str, str]], events: list[dict[str, Any]], plan: Plan
) -> list[Result]:
    types = [e["type"] for e in events]
    ids = [int(m["id"]) for m in messages if "id" in m]
    done = events[-1] if events else {}
    results: list[Result] = [
        (
            "stream is ordered and complete",
            bool(events)
            and types[0] == "run_started"
            and types[-1] == "run_completed"
            and ids == list(range(1, len(events) + 1)),
            f"{len(events)} events",
        ),
        ("a plan was announced", "plan_ready" in types, ""),
        (
            f"final status is {plan.status}",
            done.get("data", {}).get("status") == plan.status,
            str(done.get("data", {}).get("status")),
        ),
    ]
    for needed in sorted(plan.must_see):
        results.append((f"saw {needed}", needed in types, ""))

    notifier_ids = {
        e["data"]["step_id"]
        for e in events
        if e["type"] == "step_started" and e["data"].get("tool") == "notifier"
    }
    announced = any(
        e["type"] == "step_succeeded" and e["data"].get("step_id") in notifier_ids
        for e in events
    )
    results.append(
        (
            "chat message sent" if plan.announces else "NO chat message sent",
            announced == plan.announces,
            f"announced={announced}",
        )
    )
    return results


def check_report(report: dict[str, Any] | None, plan: Plan) -> list[Result]:
    if not report:
        return [("report exists", False, "missing")]
    created = report.get("created") or []
    links_ok = all(str(c.get("url", "")).startswith("http") for c in created)
    if plan.status == "completed":
        shape_ok = (
            len(created) >= 2 and links_ok
        )  # at least the Notion page and Drive file
    else:
        shape_ok = created == []  # nothing is claimed after a rollback
    return [
        ("report status matches", report.get("status") == plan.status, ""),
        ("report has a headline", bool(report.get("headline")), ""),
        ("created items match the outcome", shape_ok, f"{len(created)} link(s)"),
    ]


def check_api_agreement(
    run_id: str,
    detail: dict[str, Any],
    listed: list[dict[str, Any]],
    download: dict[str, Any] | None,
    plan: Plan,
) -> list[Result]:
    item = next((i for i in listed if i["run_id"] == run_id), None)
    return [
        ("run detail is finished", detail.get("status") == plan.status, ""),
        (
            "trace link is present",
            str(detail.get("trace_url", "")).startswith("http"),
            "",
        ),
        (
            "run is in the history list",
            item is not None and item["status"] == plan.status,
            "",
        ),
        (
            "report download equals the stored report",
            download == detail.get("report"),
            "",
        ),
    ]


def check_replay(
    live: list[dict[str, str]],
    replayed: list[dict[str, str]],
    resumed: list[dict[str, str]],
) -> list[Result]:
    return [
        (
            "a later replay returns the same events",
            replayed == live,
            f"{len(replayed)} events",
        ),
        (
            "Last-Event-ID resumes without repeats",
            resumed == live[-2:],
            f"{len(resumed)} events",
        ),
    ]


def show(results: Iterable[Result]) -> bool:
    ok = True
    for name, passed, detail in results:
        print(
            f"  [{'PASS' if passed else 'FAIL'}] {name}"
            + (f": {detail}" if detail else "")
        )
        ok = ok and passed
    return ok


# --------------------------------------------------------------------------- the server


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class Server:
    """The real app under uvicorn. Its output goes to a file, shown only on failure."""

    port: int = field(default_factory=free_port)
    _process: subprocess.Popen[bytes] | None = None
    _log: Any = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # `Self` needs Python 3.11; the project supports 3.10, so name the class.
    def __enter__(self) -> "Server":  # noqa: PYI034
        self._log = tempfile.TemporaryFile()
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=BACKEND_DIR,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                break
            try:
                if (
                    httpx.get(f"{self.base_url}/api/health", timeout=2).status_code
                    == 200
                ):
                    return self
            except httpx.HTTPError:
                time.sleep(0.5)
        self.print_log()
        self.__exit__()
        raise RuntimeError("the server did not start")

    def __exit__(self, *_: object) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def print_log(self, tail: int = 40) -> None:
        if self._log is None:
            return
        self._log.seek(0)
        lines = self._log.read().decode("utf-8", "replace").splitlines()
        print("--- server output (last lines) ---")
        print("\n".join(lines[-tail:]))


# --------------------------------------------------------------------------- one live run


def read_events(
    client: httpx.Client, run_id: str, **headers: str
) -> list[dict[str, str]]:
    with client.stream(
        "GET", f"/api/runs/{run_id}/events", headers=headers
    ) as response:
        response.raise_for_status()
        return parse_sse(response.iter_lines())


def wait_until_finished(client: httpx.Client, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + STORE_SETTLE_SECONDS
    while True:
        detail: dict[str, Any] = client.get(f"/api/runs/{run_id}").json()
        if detail["status"] != "running" or time.monotonic() > deadline:
            return detail
        time.sleep(0.5)


def run_plan(
    client: httpx.Client, plan: Plan, example: dict[str, str]
) -> tuple[bool, dict[str, Any]]:
    print(f"\n=== {plan.template_id} (simulate: {plan.simulate or 'off'}) ===")
    body: dict[str, Any] = {"template_id": plan.template_id, "values": example}
    if plan.simulate:
        body["simulate"] = plan.simulate
    started = time.monotonic()
    response = client.post("/api/runs", json=body)
    if response.status_code != 202:
        return (
            show(
                [
                    (
                        "run started",
                        False,
                        f"HTTP {response.status_code}: {response.text[:200]}",
                    )
                ]
            ),
            {},
        )
    run_id: str = response.json()["run_id"]
    print(f"  run id {run_id}")

    live = read_events(client, run_id)
    elapsed = time.monotonic() - started
    events = decode(live)
    detail = wait_until_finished(client, run_id)
    listed = client.get("/api/runs", params={"limit": 100}).json()
    download = client.get(f"/api/runs/{run_id}/report")
    replayed = read_events(client, run_id)
    resumed = read_events(client, run_id, **{"Last-Event-ID": str(len(live) - 2)})

    results = [
        *check_stream(live, events, plan),
        *check_report(detail.get("report"), plan),
        *check_api_agreement(
            run_id,
            detail,
            listed,
            download.json() if download.status_code == 200 else None,
            plan,
        ),
        *check_replay(live, replayed, resumed),
    ]
    ok = show(results)
    print(f"  took {elapsed:.1f}s; Langfuse trace: {detail.get('trace_url')}")
    return ok, detail


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live end-to-end check through the API."
    )
    parser.add_argument(
        "--live", action="store_true", help="required: acknowledges real writes"
    )
    if not parser.parse_args(argv).live:
        parser.error(
            "refusing to run without --live: this creates real Notion pages and Drive files "
            "and posts real chat messages."
        )
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    all_ok = True
    created: list[str] = []
    traces: list[str] = []
    with Server() as server:
        client = httpx.Client(
            base_url=server.base_url,
            timeout=httpx.Timeout(15.0, read=RUN_TIMEOUT_SECONDS),
        )
        try:
            health = client.get("/api/health").json()
            print(
                f"Server up. LLM: {health['llm_provider']}; notifier: {health['notifier_provider']}"
            )
            examples = {
                t["id"]: t["example"] for t in client.get("/api/templates").json()
            }
            for index, plan in enumerate(PLANS):
                if index:
                    print(
                        f"\n(waiting {WAIT_BETWEEN_RUNS_SECONDS}s: the free LLM tier allows about one run a minute)"
                    )
                    time.sleep(WAIT_BETWEEN_RUNS_SECONDS)
                ok, detail = run_plan(client, plan, examples[plan.template_id])
                all_ok = all_ok and ok
                traces.append(str(detail.get("trace_url")))
                created += [
                    f"{c.get('label')}: {c.get('url')}"
                    for c in (detail.get("report") or {}).get("created", [])
                ]
        except httpx.HTTPError as error:
            print(f"[FAIL] request error: {type(error).__name__}")
            all_ok = False
        finally:
            client.close()
        if not all_ok:
            server.print_log()

    print(
        "\n--- Langfuse traces: open each and check the name, tags, steps and scores ---"
    )
    print("\n".join(traces))
    print("\n--- left behind in your sandbox (delete by hand if you like) ---")
    print("\n".join(created) or "(nothing)")
    print("\nALL CHECKS PASSED" if all_ok else "\nSOME CHECKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
