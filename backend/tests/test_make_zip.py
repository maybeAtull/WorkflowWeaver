"""The packaging script: right contents, nothing secret, and a bad file stops the build."""

import zipfile
from pathlib import Path

import pytest

from scripts import make_zip

LANGFUSE_LIKE = (
    "sk-lf-" + "0123abcd-0123-0123-0123-0123456789ab"
)  # shaped like a key, but made up


def project(tmp_path: Path) -> Path:
    """A tiny fake project with every kind of file the script must keep or skip."""
    files = {
        "README.md": "# hi",
        ".env.example": "LLM_API_KEY=your_llm_api_key_here\nLANGFUSE_SECRET_KEY=sk-lf-your-secret-key\n",
        ".env": "LLM_API_KEY=real",
        "credentials.json": "{}",
        "CLAUDE.md": "private notes",
        "IMPLEMENTATION_PLAN.md": "plan",
        "docker-compose.yml": "name: x",
        "docker/drive-mcp/Dockerfile": "FROM node",
        "docs/architecture.png": "png",
        "docs/other.md": "not wanted",
        "backend/app/main.py": "print(1)",
        "backend/.venv/lib/x.py": "venv",
        "backend/data/run.db": "db",
        "backend/app/__pycache__/x.pyc": "pyc",
        "frontend/src/App.tsx": "export {}",
        "frontend/node_modules/react/index.js": "dep",
        "frontend/dist/index.html": "built",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def names(files: list[Path], root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in files}


def test_only_the_wanted_files_are_collected(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert names(make_zip.collect_files(root), root) == {
        "README.md",
        ".env.example",
        "docker-compose.yml",
        "docker/drive-mcp/Dockerfile",
        "docs/architecture.png",
        "backend/app/main.py",
        "frontend/src/App.tsx",
    }


def test_a_clean_project_builds_a_zip_with_those_files(tmp_path: Path) -> None:
    root = project(tmp_path / "p")
    out = tmp_path / "out" / "x.zip"
    make_zip.build_zip(out, root)
    with zipfile.ZipFile(out) as archive:
        assert "README.md" in archive.namelist()
        assert not any(
            word in name
            for name in archive.namelist()
            for word in (".venv", "node_modules", ".db", "credentials", "CLAUDE")
        )
        assert ".env" not in archive.namelist()


def test_a_key_shaped_string_stops_the_build_and_is_not_printed(tmp_path: Path) -> None:
    root = project(tmp_path / "p")
    (root / "backend/app/main.py").write_text(
        f'KEY = "{LANGFUSE_LIKE}"', encoding="utf-8"
    )
    out = tmp_path / "x.zip"
    with pytest.raises(SystemExit) as stopped:
        make_zip.build_zip(out, root)
    message = str(stopped.value)
    assert "backend/app/main.py:1" in message and "Langfuse key" in message
    assert LANGFUSE_LIKE not in message  # the value is never shown
    assert not out.exists()


def test_the_placeholders_in_env_example_are_not_flagged(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert make_zip.scan_for_secrets(make_zip.collect_files(root), root) == []


def test_a_forbidden_file_name_is_reported() -> None:
    bad = make_zip.forbidden_files(
        [Path("a/.env"), Path("a/tokens.json"), Path("a/.env.example"), Path("a/ok.py")]
    )
    assert [p.name for p in bad] == [".env", "tokens.json"]


def test_the_real_project_scans_clean() -> None:
    """What would actually ship has no secrets and no forbidden names."""
    files = make_zip.collect_files()
    assert make_zip.forbidden_files(files) == []
    assert make_zip.scan_for_secrets(files) == []
    assert {"README.md", ".env.example", "docs/architecture.png"} <= names(
        files, make_zip.ROOT
    )
