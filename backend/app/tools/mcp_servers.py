"""The concrete MCP servers we use (Notion, Google Drive): how to start them and which tools
the adapters may call. The Docker commands are the ones proven in the Phase 0 smoke tests.
"""

from app.config import Settings
from app.tools.mcp_session import McpServerSpec

NOTION_SERVER = "notion"
DRIVE_SERVER = "drive"

NOTION_IMAGE = "mcp/notion"
# Built from docker/drive-mcp/Dockerfile (the upstream project publishes no image).
DRIVE_IMAGE = "workflowweaver-drive-mcp"

# Whitelists: the only tools our adapters need, out of 24 (Notion) and 130 (Drive) offered.
NOTION_ALLOWED_TOOLS = frozenset(
    {"API-post-page", "API-patch-page", "API-retrieve-a-page"}
)
DRIVE_ALLOWED_TOOLS = frozenset({"listFolder", "createTextFile", "deleteItem"})

# Narrowest scopes that cover our use; the server's default also asks for Sheets, Slides
# and Calendar.
DRIVE_SCOPES = "drive,documents"


def notion_spec(settings: Settings) -> McpServerSpec:
    token = settings.notion_token.get_secret_value()
    return McpServerSpec(
        name=NOTION_SERVER,
        command="docker",
        # "-e NOTION_TOKEN" (no value) makes Docker copy it from our environment, so the
        # token never appears on a command line where a process list could show it.
        args=("run", "--rm", "-i", "-e", "NOTION_TOKEN", NOTION_IMAGE),
        env={"NOTION_TOKEN": token},
        allowed_tools=NOTION_ALLOWED_TOOLS,
        secrets=(token,),
    )


def drive_spec(settings: Settings) -> McpServerSpec:
    credentials = settings.google_credentials_file
    tokens = settings.google_token_directory
    return McpServerSpec(
        name=DRIVE_SERVER,
        command="docker",
        args=(
            "run",
            "--rm",
            "-i",
            # Read-only OAuth client file.
            "-v",
            f"{credentials.as_posix()}:/config/gcp-oauth.keys.json:ro",
            # The token DIRECTORY, writable: the server refreshes tokens by atomic rename,
            # which fails on a single-file bind mount.
            "-v",
            f"{tokens.as_posix()}:/config/tokens",
            "-e",
            "GOOGLE_DRIVE_MCP_SCOPES",
            DRIVE_IMAGE,
        ),
        env={"GOOGLE_DRIVE_MCP_SCOPES": DRIVE_SCOPES},
        allowed_tools=DRIVE_ALLOWED_TOOLS,
    )


def default_specs(settings: Settings) -> list[McpServerSpec]:
    return [notion_spec(settings), drive_spec(settings)]
