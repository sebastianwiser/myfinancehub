"""Minimal .env support. Values never leave the process environment or the .env file."""

import os
from pathlib import Path

ENV_FILE_VAR = "MAINHUB_ENV_FILE"


def env_file() -> Path:
    return Path(os.environ.get(ENV_FILE_VAR, ".env"))


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ. Real environment wins; empty values are skipped."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        value = value.strip().strip("'\"")
        if value:
            os.environ.setdefault(key.strip(), value)


def append_to_list_var(path: Path, key: str, item: str) -> None:
    """Add `item` to a comma-separated KEY in the .env file (creating the line if needed)."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            items = [x for x in line.partition("=")[2].split(",") if x.strip()]
            if item not in items:
                items.append(item)
            lines[i] = f"{key}={','.join(items)}"
            break
    else:
        lines.append(f"{key}={item}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def set_var(path: Path, key: str, value: str) -> None:
    """Set KEY=value in the .env file, replacing any existing line."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)
