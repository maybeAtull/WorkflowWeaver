"""Print the tool catalogue exactly as the planner (the LLM) will see it, and its size.

Run from the backend/ folder:  python -m scripts.show_catalogue

Offline: builds the registry from your settings but starts no containers and calls no service
(MCP servers start lazily, on a tool's first call). Never prints secrets.
"""

import sys

from pydantic import ValidationError

from app.config import get_settings
from app.tools.mcp_servers import default_specs
from app.tools.mcp_session import McpSessionManager
from app.tools.registry import build_registry


def main() -> int:
    try:
        settings = get_settings()
    except ValidationError as exc:
        # Field names and messages only; never the offending input values.
        problems = [
            f"{'.'.join(str(p) for p in e['loc']) or 'settings'}: {e['msg']}"
            for e in exc.errors()
        ]
        print("[FAIL] config loads: " + "; ".join(problems))
        return 1

    # Never entered as a context manager, so no server is started.
    registry = build_registry(settings, McpSessionManager(default_specs(settings)))
    catalogue = registry.render_catalogue()
    print(catalogue)
    print()
    print(
        f"{len(catalogue)} characters, roughly {len(catalogue) // 4} tokens "
        f"(notifier provider: {settings.notifier_provider})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
