import { afterEach, describe, expect, it, vi } from "vitest";

import {
  API_BASE_URL,
  ApiError,
  SERVER_UNREACHABLE,
  eventsUrl,
  getRun,
  getTemplates,
  listRuns,
  messageFromDetail,
  reportDownloadUrl,
  startRun,
} from "./client";

function respond(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(typeof body === "string" ? body : JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function failureOf(call: Promise<unknown>): Promise<ApiError> {
  try {
    await call;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error("expected the call to fail");
}

afterEach(() => vi.unstubAllGlobals());

describe("plain-language errors", () => {
  it("explains an unreachable server", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await failureOf(getTemplates());
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe(SERVER_UNREACHABLE);
    expect(error.status).toBe(0);
  });

  it("passes on the server's own sentence for an error it raised", async () => {
    respond(429, { detail: "Other workflows are still running. Please try again in a minute." });
    const error = await failureOf(startRun({ request: "x" }));
    expect(error.message).toBe(
      "Other workflows are still running. Please try again in a minute.",
    );
    expect(error.status).toBe(429);
  });

  it("turns a validation list into one sentence without the technical prefix", async () => {
    respond(422, { detail: [{ msg: "Value error, Describe the workflow you want to run." }] });
    const error = await failureOf(startRun({ request: " " }));
    expect(error.message).toBe("Describe the workflow you want to run.");
  });

  it("never shows a server error's details", async () => {
    respond(500, { detail: "Traceback: secret token abc" });
    const error = await failureOf(listRuns());
    expect(error.message).toBe("Something went wrong on the server. Please try again.");
    expect(error.message).not.toContain("secret");
  });

  it("copes with a body that is not JSON", async () => {
    respond(404, "<html>Not found</html>");
    expect((await failureOf(getRun("nope"))).message).toBe("We couldn't find that.");
    respond(400, "oops");
    expect((await failureOf(getRun("x"))).message).toMatch(/couldn't be completed/);
  });

  it("lets a cancelled request pass as an abort, not as a failure message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")),
    );
    await expect(getTemplates()).rejects.toMatchObject({ name: "AbortError" });
  });

  it("reads detail in each shape", () => {
    expect(messageFromDetail("Plain")).toBe("Plain");
    expect(messageFromDetail([{ msg: "Value error, Fix this" }])).toBe("Fix this");
    expect(messageFromDetail([])).toBeNull();
    expect(messageFromDetail([{}])).toBeNull();
    expect(messageFromDetail(null)).toBeNull();
    expect(messageFromDetail("   ")).toBeNull();
  });
});

describe("requests", () => {
  it("starts a run from a template with its values and returns the run id", async () => {
    const fetchMock = respond(202, { run_id: "abc123" });
    const id = await startRun({
      template_id: "meeting_to_tasks",
      values: { notes: "n" },
      simulate: "s2:fatal",
    });
    expect(id).toBe("abc123");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/runs`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      template_id: "meeting_to_tasks",
      values: { notes: "n" },
      simulate: "s2:fatal",
    });
  });

  it("asks for the history with a limit", async () => {
    const fetchMock = respond(200, []);
    await listRuns(5);
    expect(fetchMock.mock.calls[0][0]).toBe(`${API_BASE_URL}/api/runs?limit=5`);
  });

  it("builds the stream and download links, escaping the run id", () => {
    expect(eventsUrl("a b")).toBe(`${API_BASE_URL}/api/runs/a%20b/events`);
    expect(reportDownloadUrl("r1")).toBe(`${API_BASE_URL}/api/runs/r1/report`);
  });
});
