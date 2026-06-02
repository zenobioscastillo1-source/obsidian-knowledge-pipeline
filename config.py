"""Configuration and path-safety helpers for the Obsidian vault.

Everything that touches the filesystem goes through this module so there is a
single place that (a) knows where the vault lives and (b) guarantees a request
can never read or write outside of it.
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load VAULT_PATH (and optional IGNORED_FOLDERS) from the .env file next to this script.
load_dotenv()

# Top-level vault folders to hide from discovery (listing/searching/processing).
# Override via the IGNORED_FOLDERS env var (comma-separated). These are personal
# folders that aren't part of the knowledge pipeline.
_DEFAULT_IGNORED_FOLDERS = ("7 - File Vault", "8 - Quests")


@lru_cache(maxsize=1)
def get_vault_path() -> Path:
    """Return the validated vault root as an absolute ``Path``.

    Raises:
        RuntimeError: if ``VAULT_PATH`` is unset, does not exist, or is not a
            directory. The message tells the user exactly how to fix it.
    """
    raw = os.getenv("VAULT_PATH")
    if not raw or not raw.strip():
        raise RuntimeError(
            "VAULT_PATH is not set. Add it to your .env file, e.g.\n"
            '    VAULT_PATH=D:\\path\\to\\Your Obsidian Vault'
        )

    vault = Path(raw.strip()).expanduser().resolve()
    if not vault.exists():
        raise RuntimeError(f"VAULT_PATH does not exist: {vault}")
    if not vault.is_dir():
        raise RuntimeError(f"VAULT_PATH is not a directory: {vault}")
    return vault


@lru_cache(maxsize=1)
def get_ignored_folders() -> frozenset[str]:
    """Top-level folder names excluded from discovery, lower-cased for matching.

    Read from the ``IGNORED_FOLDERS`` env var (comma-separated) if set, else the
    built-in defaults. Comparison is case-insensitive.
    """
    raw = os.getenv("IGNORED_FOLDERS")
    names = (
        [p.strip() for p in raw.split(",")]
        if raw and raw.strip()
        else list(_DEFAULT_IGNORED_FOLDERS)
    )
    return frozenset(n.lower() for n in names if n)


def is_ignored(rel_path: Path) -> bool:
    """True if ``rel_path`` (relative to the vault root) lives under an ignored
    top-level folder. The vault root itself (``Path('.')``) is never ignored.
    """
    parts = rel_path.parts
    return bool(parts) and parts[0].lower() in get_ignored_folders()


def resolve_in_vault(relative_path: str) -> Path:
    """Resolve ``relative_path`` against the vault, refusing to escape it.

    This is the single security choke point for every vault tool. It rejects
    absolute paths and any path that — after resolving symlinks and ``..``
    segments — would land outside the vault root.

    Args:
        relative_path: A path relative to the vault root, e.g.
            ``"3 - Tags/Zettelkasten.md"``. Backslashes and forward slashes are
            both accepted. An empty string resolves to the vault root itself.

    Returns:
        The resolved absolute ``Path`` (which may or may not exist yet).

    Raises:
        ValueError: if the path is absolute or escapes the vault.
    """
    vault = get_vault_path()

    # Normalise separators so Windows-style "a\b" and POSIX "a/b" behave the
    # same, then strip leading slashes that would make it look absolute.
    cleaned = (relative_path or "").replace("\\", "/").strip().lstrip("/")

    candidate = Path(cleaned)
    if candidate.is_absolute():
        raise ValueError(
            f"Path must be relative to the vault, not absolute: {relative_path!r}"
        )

    resolved = (vault / candidate).resolve()

    # The real guard: after resolving "..", symlinks, etc., we must still be
    # inside the vault. is_relative_to is available on Python 3.9+.
    if resolved != vault and not resolved.is_relative_to(vault):
        raise ValueError(
            f"Path escapes the vault and was rejected: {relative_path!r}"
        )
    return resolved
