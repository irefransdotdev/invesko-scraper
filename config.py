"""Runtime configuration: loads .env (never committed) and exposes DB settings."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
LOCAL_DB_PATH = PROJECT_ROOT / "output" / "uitf_local.db"

_URL_KEYS = ("TURSO_DATABASE_URL", "TURSO_URL", "DATABASE_URL", "LIBSQL_URL")
_TOKEN_KEYS = ("TURSO_AUTH_TOKEN", "TURSO_TOKEN", "AUTH_TOKEN", "LIBSQL_AUTH_TOKEN")

_loaded = False


def load_env_file() -> None:
    """Parse KEY=VALUE lines from .env into os.environ (never overriding real env)."""
    global _loaded
    if _loaded or not ENV_PATH.is_file():
        _loaded = True
        return
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    _loaded = True


def _first_env(keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def db_config() -> dict:
    """Return DB settings. Uses Turso when URL is configured, else a local file.

    Only reports which variable NAMES were found — never values.
    """
    load_env_file()
    url = _first_env(_URL_KEYS)
    token = _first_env(_TOKEN_KEYS)
    found = [k for k in (*_URL_KEYS, *_TOKEN_KEYS) if os.environ.get(k, "").strip()]
    return {
        "url": url,
        "auth_token": token or "",
        "found_env_keys": found,
        "is_remote": bool(url),
        "local_path": str(LOCAL_DB_PATH),
    }
