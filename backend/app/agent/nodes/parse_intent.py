"""The parse-intent node: read the user's request into a compact `Intent` (one LLM call).

Runs before planning so that (a) the long pasted notes travel in this call only, and the planner
works from the small digest (matters on Groq's 8K tokens/minute), and (b) an unusable request
stops here, with a clear question, before any tool is touched.
"""

import logging
from typing import Any

from app.agent.deps import AgentDeps, IntentParser
from app.agent.llm_client import LlmClient, LlmError
from app.agent.nodes.plan import PlanningError, failed_update
from app.agent.prompts import INTENT_SYSTEM, intent_human_prompt
from app.agent.state import Intent, WorkflowState

logger = logging.getLogger(__name__)

# About 1,500 tokens. Longer requests are rejected, not cut: a silent cut could drop the very
# action items the user cares about.
MAX_REQUEST_CHARS = 6000
MAX_TITLE_CHARS = 80  # the schema carries no length limit (see Intent), so we trim here


def make_llm_intent_parser(client: LlmClient) -> IntentParser:
    async def parse(state: WorkflowState) -> Intent:
        request = state["user_request"].strip()
        if not request:
            raise PlanningError("Please describe the workflow you want to run.")
        if len(request) > MAX_REQUEST_CHARS:
            raise PlanningError(
                f"Your request is {len(request):,} characters long; the limit is "
                f"{MAX_REQUEST_CHARS:,}. Please shorten it (for example, paste only the "
                "relevant notes)."
            )
        try:
            intent = await client.ask(
                INTENT_SYSTEM, intent_human_prompt(request), Intent
            )
        except LlmError as error:
            raise PlanningError(str(error)) from error

        question = intent.clarification_needed.strip()
        if question:
            raise PlanningError(question)
        return intent.model_copy(
            update={"title": intent.title.strip()[:MAX_TITLE_CHARS]}
        )

    return parse


def make_parse_intent_node(deps: AgentDeps) -> Any:
    async def parse_intent(state: WorkflowState) -> dict[str, Any]:
        try:
            intent = await deps.intent_parser(state)
        except PlanningError as error:
            return failed_update(str(error))
        except Exception as error:  # noqa: BLE001
            # A parser bug must end the run cleanly. Log the type only: the message could
            # contain the user's request text.
            logger.error("Intent parser crashed: %s", type(error).__name__)
            return failed_update(
                f"Understanding the request failed unexpectedly ({type(error).__name__})"
            )
        return {"intent": intent}

    return parse_intent
