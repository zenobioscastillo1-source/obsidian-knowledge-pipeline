"""The four vault tools: search, read, create, and list.

Each tool returns a plain ``dict`` (FastMCP serialises it to JSON for the
client) and reports failures as ``{"error": ...}`` rather than raising, so the
AI client always gets something useful back instead of a stack trace.

All filesystem access is funnelled through ``config.resolve_in_vault`` so a
malicious or buggy path can never reach outside the vault.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from config import get_vault_path, is_ignored, resolve_in_vault

SNIPPET_LEN = 200


def _is_hidden(path: Path) -> bool:
    """True if any component of the path (below the vault) starts with a dot."""
    return any(part.startswith(".") for part in path.parts)


def _rel(path: Path) -> str:
    """Return ``path`` as a forward-slash string relative to the vault root."""
    return path.relative_to(get_vault_path()).as_posix()


def _snippet(text: str) -> str:
    """First SNIPPET_LEN characters of ``text``, whitespace-collapsed."""
    return " ".join(text.split())[:SNIPPET_LEN]


def _iso_mtime(path: Path) -> str:
    """ISO-8601 local-time last-modified timestamp for ``path``
    (e.g. ``2026-05-28T14:30:00``), matching the vault spec."""
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def register_vault_tools(mcp: FastMCP) -> None:
    """Register all four vault tools on the given FastMCP instance."""

    @mcp.tool()
    def search_vault(
        query: str,
        folder: str | None = None,
        search_content: bool = False,
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Search the vault for notes by filename, and optionally by content.

        Args:
            query: Text to look for (case-insensitive substring match).
            folder: Restrict the search to this folder, e.g. ``"3 - Tags"``
                (optional; searches the whole vault if omitted).
            search_content: If true, also read each .md file and match on its
                text, not just its filename (default false).
            max_results: Maximum number of results to return (default 10).

        Folders in the ignore-list (see config.IGNORED_FOLDERS) are skipped,
        unless you scope the search into one of them via ``folder``.
        """
        try:
            root = resolve_in_vault(folder or "")
        except ValueError as exc:
            return {"error": str(exc)}
        if not root.is_dir():
            return {"error": f"Folder not found: {folder!r}"}

        needle = query.lower()
        results: list[dict[str, str]] = []
        total_found = 0
        # Only filter ignored folders when the search isn't already scoped into one.
        apply_ignore = not is_ignored(root.relative_to(get_vault_path()))

        for md in sorted(root.rglob("*.md")):
            rel = md.relative_to(get_vault_path())
            if _is_hidden(rel) or (apply_ignore and is_ignored(rel)):
                continue

            text: str | None = None
            matched = needle in md.name.lower()

            if not matched and search_content:
                try:
                    text = md.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                matched = needle in text.lower()

            if not matched:
                continue

            total_found += 1
            if len(results) >= max_results:
                continue

            if text is None:
                try:
                    text = md.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""

            results.append(
                {
                    "path": rel.as_posix(),
                    "filename": md.name,
                    "folder": rel.parent.as_posix() if rel.parent != Path(".") else "",
                    "snippet": _snippet(text),
                }
            )

        return {"results": results, "total_found": total_found}

    @mcp.tool()
    def read_note(path: str) -> dict[str, Any]:
        """Read the full content of a note.

        Args:
            path: Path relative to the vault root, e.g.
                ``"3 - Tags/Zettelkasten.md"``.
        """
        try:
            target = resolve_in_vault(path)
        except ValueError as exc:
            return {"error": str(exc)}

        if not target.exists() or not target.is_file():
            return {"error": f"Note not found: {path!r}"}
        if target.suffix.lower() != ".md":
            return {"error": f"Not a markdown (.md) file: {path!r}"}

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"error": f"Could not read note: {exc}"}

        return {
            "path": _rel(target),
            "content": content,
            "size_bytes": target.stat().st_size,
            "last_modified": _iso_mtime(target),
        }

    @mcp.tool()
    def create_note(
        path: str,
        content: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Create a new note in the vault, making parent folders as needed.

        Args:
            path: Path (including filename) relative to the vault root, e.g.
                ``"2 - Source Material/Module 1 Introduction.md"``.
            content: Full markdown content to write (UTF-8).
            overwrite: If false (default), refuse to overwrite an existing file.
        """
        try:
            target = resolve_in_vault(path)
        except ValueError as exc:
            return {"error": str(exc)}

        if target.exists() and not overwrite:
            return {
                "error": (
                    f"Note already exists: {path!r}. "
                    "Pass overwrite=true to replace it."
                ),
                "created": False,
            }

        # Obsidian convention favours spaces over underscores in filenames.
        warning = None
        if "_" in target.name:
            warning = (
                f"Filename {target.name!r} uses underscores; Obsidian notes "
                "conventionally use spaces."
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return {"error": f"Could not write note: {exc}", "created": False}

        result: dict[str, Any] = {
            "created": True,
            "path": _rel(target),
            "size_bytes": target.stat().st_size,
        }
        if warning:
            result["warning"] = warning
        return result

    @mcp.tool()
    def list_folder(path: str = "", recursive: bool = False) -> dict[str, Any]:
        """List files and subfolders in a vault directory.

        Args:
            path: Folder relative to the vault root (default ``""`` = the vault
                root itself).
            recursive: If true, walk subfolders too (default false).

        Folders in the ignore-list (see config.IGNORED_FOLDERS) are skipped,
        unless you list one of them directly via ``path``.
        """
        try:
            root = resolve_in_vault(path)
        except ValueError as exc:
            return {"error": str(exc)}
        if not root.is_dir():
            return {"error": f"Folder not found: {path!r}"}

        walker = root.rglob("*") if recursive else root.glob("*")
        items: list[dict[str, Any]] = []
        total_files = 0
        total_folders = 0
        # Only filter ignored folders when not listing one of them directly.
        apply_ignore = not is_ignored(root.relative_to(get_vault_path()))

        for entry in walker:
            rel = entry.relative_to(get_vault_path())
            if _is_hidden(rel) or (apply_ignore and is_ignored(rel)):
                continue

            if entry.is_dir():
                total_folders += 1
                try:
                    count = sum(1 for c in entry.iterdir() if not c.name.startswith("."))
                except OSError:
                    count = 0
                items.append(
                    {
                        "name": _rel(entry) if recursive else entry.name,
                        "type": "folder",
                        "item_count": count,
                    }
                )
            elif entry.is_file():
                total_files += 1
                items.append(
                    {
                        "name": _rel(entry) if recursive else entry.name,
                        "type": "file",
                        "size_bytes": entry.stat().st_size,
                    }
                )

        # Folders first, then files; alphabetical within each group.
        items.sort(key=lambda i: (i["type"] != "folder", i["name"].lower()))

        rel_root = root.relative_to(get_vault_path()).as_posix()
        return {
            "path": "" if rel_root == "." else rel_root,
            "items": items,
            "total_files": total_files,
            "total_folders": total_folders,
        }
