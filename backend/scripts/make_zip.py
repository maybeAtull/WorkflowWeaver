"""Build the submission ZIP, after scanning what goes into it for secrets.

Run from the backend/ folder:  python -m scripts.make_zip [--out PATH]

The ZIP holds exactly what the assessment asks for, at its top level:
  README.md, .env.example, frontend/, backend/, docs/architecture.png
plus docker-compose.yml and docker/ (the README's setup builds the Drive image from them) and
docs/architecture.svg (the diagram's source).

It is a whitelist: anything not named above (the .env file, credentials.json, token files, the
plan, chat logs, CLAUDE.md) can never end up in the ZIP. Inside backend/ and frontend/ the usual
generated folders and databases are skipped. Before anything is written the files are scanned for
key-shaped strings; a hit stops the build and is reported as file:line and the kind of secret,
NEVER the value.
"""

import argparse
import re
import sys
import zipfile
from pathlib import Path

from app.config import ENV_FILE

ROOT = ENV_FILE.parent
DEFAULT_OUT = ROOT.parent / "WorkflowWeaver.zip"

INCLUDE_FILES = [
    "README.md",
    ".env.example",
    "docker-compose.yml",
    "docs/architecture.png",
    "docs/architecture.svg",
]
INCLUDE_DIRS = ["backend", "frontend", "docker"]

# Folder names skipped anywhere below an included folder.
SKIP_DIRS = {
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    ".vite",
    "coverage",
    "data",  # backend/data: the run-history database
    ".playwright-mcp",
}
SKIP_SUFFIXES = (
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite3",
    ".pyc",
    ".log",
    ".pem",
)
# Names that must never ship, wherever they are.
FORBIDDEN_NAMES = (
    "credentials",
    "tokens.json",
    "token.json",
    "client_secret",
    "gcp-oauth",
)
ALLOWED_ENV_NAMES = {".env.example"}

# Shapes of real keys. A placeholder such as "sk-lf-your-secret-key" does not match, so
# .env.example passes while a pasted key would not.
SECRET_PATTERNS = {
    "Langfuse key": r"\b[sp]k-lf-[0-9a-f]{8}-[0-9a-f]{4}-",
    "Slack webhook URL": r"hooks\.slack\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{16,}",
    "Teams / Power Automate webhook URL": r"(webhook\.office\.com/webhookb2/|logic\.azure\.com(:443)?/workflows/)[A-Za-z0-9@\-/]{16,}",
    "Google API key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "Google OAuth client secret": r"\bGOCSPX-[A-Za-z0-9_\-]{20,}",
    "Notion token": r"\b(ntn_|secret_)[A-Za-z0-9]{30,}",
    "Groq key": r"\bgsk_[A-Za-z0-9]{20,}",
    "Anthropic key": r"\bsk-ant-[A-Za-z0-9_\-]{20,}",
    "Private key block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "Refresh token": r"\b1//[0-9A-Za-z_\-]{40,}",
}
TEXT_SUFFIXES = {
    "",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".txt",
    ".html",
    ".css",
    ".svg",
    ".example",
    ".ini",
    ".cfg",
    ".env",
}


def _skipped(path: Path, base: Path) -> bool:
    relative = path.relative_to(base)
    if any(part in SKIP_DIRS for part in relative.parts[:-1]):
        return True
    return path.name.endswith(SKIP_SUFFIXES)


def collect_files(root: Path = ROOT) -> list[Path]:
    """Everything that goes into the ZIP, as sorted absolute paths."""
    files: list[Path] = []
    for name in INCLUDE_FILES:
        path = root / name
        if path.is_file():
            files.append(path)
    for folder in INCLUDE_DIRS:
        base = root / folder
        if not base.is_dir():
            continue
        files.extend(
            path
            for path in base.rglob("*")
            if path.is_file() and not _skipped(path, base)
        )
    return sorted(set(files))


def forbidden_files(files: list[Path]) -> list[Path]:
    """Files whose NAME says secret (credentials, tokens, a real .env). Should never be collected."""
    bad = []
    for path in files:
        name = path.name.lower()
        is_env = (name == ".env" or name.startswith(".env.")) and (
            name not in ALLOWED_ENV_NAMES
        )
        if is_env or any(word in name for word in FORBIDDEN_NAMES):
            bad.append(path)
    return bad


def scan_for_secrets(files: list[Path], root: Path = ROOT) -> list[str]:
    """Findings as 'path:line: kind'. The matched text is never included."""
    compiled = {kind: re.compile(pattern) for kind, pattern in SECRET_PATTERNS.items()}
    findings: list[str] = []
    for path in files:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            for kind, pattern in compiled.items():
                if pattern.search(line):
                    where = path.relative_to(root).as_posix()
                    findings.append(f"{where}:{number}: looks like a {kind}")
    return findings


def build_zip(out: Path, root: Path = ROOT) -> list[Path]:
    """Scan, then write the ZIP. Raises SystemExit (with the findings) instead of writing a bad ZIP."""
    files = collect_files(root)
    problems = [
        f"{p.relative_to(root).as_posix()}: forbidden file name"
        for p in forbidden_files(files)
    ]
    problems += scan_for_secrets(files, root)
    if problems:
        raise SystemExit("NOT BUILT. Possible secrets:\n  " + "\n  ".join(problems))
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the submission ZIP.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    files = build_zip(args.out)
    size = args.out.stat().st_size / 1024
    print(f"Wrote {args.out} ({len(files)} files, {size:,.0f} KB). Secret scan: clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
