"""Configuration and path-safety helpers for the Obsidian vault.

Everything that touches the filesystem goes through this module so there is a
single place that (a) knows where the vault lives and (b) guarantees a request
can never read or write outside of it.
"""

import os
import shutil
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load VAULT_PATH (and optional IGNORED_FOLDERS) from the .env file next to this script.
load_dotenv()

# Top-level vault folders to hide from discovery (listing/searching/processing).
# Override via the IGNORED_FOLDERS env var (comma-separated). These are personal
# folders that aren't part of the knowledge pipeline.
_DEFAULT_IGNORED_FOLDERS = ("7 - File Vault", "8 - Quests")

# Central vault folder where the screenshot feature saves captured images. A
# single shared folder (per user decision) keeps every source's figures in one
# place, embeddable from any note via ``![[<name>.png]]``. Override with the
# SCREENSHOTS_FOLDER env var.
_DEFAULT_SCREENSHOTS_FOLDER = "2 - Source Material/Screenshots"


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


def get_screenshots_folder() -> str:
    """Vault-relative folder where captured screenshots are saved.

    Read from the ``SCREENSHOTS_FOLDER`` env var if set, else the built-in
    default (``2 - Source Material/Screenshots``). Always returned as a clean,
    forward-slash, vault-relative string.
    """
    raw = os.getenv("SCREENSHOTS_FOLDER")
    folder = raw.strip() if raw and raw.strip() else _DEFAULT_SCREENSHOTS_FOLDER
    return folder.replace("\\", "/").strip("/")


def resolve_image_target(relative_path: str) -> Path:
    """Resolve a screenshot's save path inside the vault, with extra guards.

    Builds on :func:`resolve_in_vault` (no traversal, vault-only) and then
    additionally refuses to write into an ignored personal folder — the
    screenshot feature should never deposit images in ``7 - File Vault`` or
    ``8 - Quests``.

    Raises:
        ValueError: if the path is absolute, escapes the vault, or lands inside
            an ignored top-level folder.
    """
    resolved = resolve_in_vault(relative_path)
    rel = resolved.relative_to(get_vault_path())
    if is_ignored(rel):
        raise ValueError(
            f"Refusing to save a screenshot into an ignored folder: {relative_path!r}"
        )
    return resolved


@lru_cache(maxsize=8)
def find_binary(name: str) -> str | None:
    """Return the full path to a system binary (e.g. ``ffmpeg``), or ``None``.

    Used so the YouTube frame path can return a clean ``{"error": ...}`` when a
    binary is missing instead of crashing. Cached because PATH lookups are cheap
    but called per request.
    """
    return shutil.which(name)


@lru_cache(maxsize=1)
def find_ffmpeg() -> str | None:
    """Return a usable ffmpeg executable path, or ``None``.

    Prefers a system ffmpeg on ``PATH`` (so power users keep their own build),
    then falls back to the static binary bundled with the ``imageio-ffmpeg``
    pip package. The fallback means the YouTube frame path works after a plain
    ``pip install`` / ``uv sync`` — no separate system-wide ffmpeg install — which
    keeps the project's "clone and run" profile intact.
    """
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 — package missing or no bundled binary
        return None
    return exe if exe and Path(exe).exists() else None
