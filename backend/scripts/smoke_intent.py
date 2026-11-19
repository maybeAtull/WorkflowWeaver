"""Smoke test: one REAL intent-parsing call through the agent's own LLM client.

Run from the backend/ folder:  python -m scripts.smoke_intent

Makes exactly one LLM call (about 1.5K tokens) with the synthetic "Project Falcon" notes, then
prints the resulting Intent. Never prints the API key. Exits non-zero on any failure.
"""

import asyncio
import sys

from app.agent.llm_client import LlmClient
from app.agent.nodes.parse_intent import make_llm_intent_parser
from app.agent.nodes.plan import PlanningError
from app.agent.state import new_state
from app.config import get_settings
from app.llm import get_llm
from scripts.smoke_llm import SAMPLE_NOTES

REQUEST = (
    "Please put these meeting notes into a Notion page with the action items, save a copy "
    "in Google Drive, and tell the team in chat.\n\n" + SAMPLE_NOTES
)


async def main() -> int:
    settings = get_settings()
    print(f"provider={settings.llm_provider}, model={settings.llm_model_name}")
    parse = make_llm_intent_parser(LlmClient(lambda: get_llm(settings)))
    try:
        intent = await parse(new_state("smoke", REQUEST))
    except PlanningError as error:  # carries a message that is safe to print
        print(f"[FAIL] intent: {error}")
        return 1
    print("[PASS] intent parsed")
    print(intent.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
