"""Canvas tools: turn a course folder into a holistic Obsidian Canvas map.

Obsidian's Canvas (`.canvas`) is a JSON file (the open *JSONCanvas* format) that
the app renders as an infinite, zoomable whiteboard of nodes and edges. It is the
perfect surface for a **zoomed-out view of a whole topic**: group the notes into
labelled, colour-coded regions and the structure of the subject becomes legible
at a glance — exactly what a stack of individual module notes cannot show.

The headline design choice (per user preference): a holistic map should show each
note's **essence, not its full text**. Embedding a whole note — frontmatter
properties and all — into a card defeats the point of zooming out. So by default
each note is rendered as a compact **summary card**: a clickable ``[[wikilink]]``
title plus a one-line essence (pulled from the note's ``summary`` frontmatter, or
supplied by the caller). You can still click through to the full note in Obsidian.
Pass ``note_style="embed"`` to get the old full-note file nodes instead.

Tools:

* ``create_canvas`` — hand it a title and a list of **columns** (themed clusters:
  a label, a colour, some notes, a key-idea card). It does the layout maths,
  resolves each note's summary, wraps each cluster in a labelled Group, wires the
  hierarchy, and writes a valid ``.canvas``. You supply the *meaning*; it supplies
  a clean, aligned, holistic map.
* ``read_canvas`` — read a ``.canvas`` back (``read_note`` only accepts ``.md``).

Design mirrors the rest of the pipeline: all filesystem access goes through
``config.resolve_in_vault``; every tool returns plain data and reports failures
as ``{"error": ...}`` instead of raising.
"""

import json
import math
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from config import get_vault_path, resolve_in_vault

# --------------------------------------------------------------------------- #
# Layout constants — tuned so the result reads well both zoomed-in (you can read
# a card) and zoomed-out (the coloured Group regions carry the structure).
# --------------------------------------------------------------------------- #
_TITLE_W, _TITLE_H = 1120, 150
_INDEX_W, _INDEX_H = 1000, 380       # embed-style overview/index file node
_ITEM_W = 480                        # shared width for note cards / file nodes
_NOTE_H = 260                        # an embedded note file node
_MIN_CARD_H = 120                    # floor for an auto-sized text card
_GAP_Y = 28                          # vertical gap between items in a column
_PAD = 40                            # padding between a Group's edge and its items
_COL_GAP = 160                       # horizontal gap between Group columns
_TITLE_TO_COLS = 120                 # gap below the title/index before the columns

_PRESET_COLORS = {"1", "2", "3", "4", "5", "6"}  # red, orange, yellow, green, cyan, purple


def _is_valid_color(c: Any) -> bool:
    """A canvas colour is a preset id ``"1"``–``"6"`` or a ``#rrggbb`` hex."""
    if not isinstance(c, str) or not c:
        return False
    if c in _PRESET_COLORS:
        return True
    return c.startswith("#") and len(c) in (4, 7)


def _norm_note(entry: Any) -> dict[str, Any]:
    """Normalise a note entry to ``{file, id, title, summary}``.

    Accepts a bare vault-relative path string, or a dict with any of
    ``file`` / ``id`` / ``title`` / ``summary``.
    """
    if isinstance(entry, str):
        return {"file": entry, "id": None, "title": None, "summary": None}
    if isinstance(entry, dict):
        return {"file": entry.get("file", ""), "id": entry.get("id"),
                "title": entry.get("title"), "summary": entry.get("summary")}
    return {"file": "", "id": None, "title": None, "summary": None}


def _as_card(entry: Any) -> tuple[str, str | None]:
    """Normalise a card entry to ``(markdown_text, optional_id)``."""
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        return entry.get("text", ""), entry.get("id")
    return "", None


def _prettify_title(stem: str) -> str:
    """``"Module 5 The Self"`` → ``"Module 5 · The Self"`` for nicer card titles."""
    m = re.match(r"^(Module\s+\d+)\s+(.+)$", stem)
    return f"{m.group(1)} · {m.group(2)}" if m else stem


def _read_frontmatter_summary(path: Path) -> str:
    """Return the ``summary:`` value from a note's YAML frontmatter, or ``""``.

    A deliberately tiny parser (no YAML dependency): reads the leading
    ``---``-delimited block and returns the first ``summary:`` line's value with
    any surrounding quotes stripped.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    for line in text[3:end].splitlines():
        s = line.strip()
        if s.lower().startswith("summary:"):
            val = s[len("summary:"):].strip()
            if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                val = val[1:-1]
            return val
    return ""


def _summary_card_text(stem: str, title: str | None, summary: str | None) -> str:
    """The markdown for an essence card: a clickable wikilink + the summary."""
    disp = title or _prettify_title(stem)
    text = f"**[[{stem}|{disp}]]**"
    if summary:
        text += f"\n\n{summary}"
    return text


def _estimate_text_height(text: str, width: int) -> int:
    """Estimate the rendered height (px) of a text card so it fits its content.

    Essence summaries vary from one line to a few hundred words; a fixed card
    height would either clip the long ones or leave the short ones cavernous. We
    approximate the wrapped line count (chars-per-line from the card width) and
    size the box to it, so every card is exactly as tall as it needs to be.
    """
    chars_per_line = max(16, (width - 36) // 8)   # ~8px per character at body size
    lines = 0
    for para in text.split("\n"):
        if not para.strip():
            lines += 1                            # a blank separator line
        else:
            lines += max(1, math.ceil(len(para) / chars_per_line))
    return max(_MIN_CARD_H, 24 + lines * 21 + 18)


def build_canvas_doc(
    title: str,
    columns: list[dict[str, Any]],
    subtitle: str = "",
    index_note: str = "",
    index_summary: str = "",
    links: list[dict[str, Any]] | None = None,
    note_style: str = "summary",
) -> dict[str, Any]:
    """Compute a full canvas document (``{"nodes": [...], "edges": [...]}``).

    Pure layout engine, no I/O. Lays the columns out left-to-right as labelled
    Group regions; inside each, stacks the notes then the key-idea cards,
    vertically chained; wires the title/index hub to each column's lead, and any
    caller ``links``.

    ``note_style``:
      * ``"summary"`` (default) — each note is a compact essence card: a
        ``[[wikilink]]`` title plus its ``summary`` text (passed in via each
        note's ``summary`` field). The holistic view shows essence, not full text.
      * ``"embed"`` — each note is a full file node (renders the whole note).

    Node ids: title ``"title"``, index ``"index"``; an unlabelled note/card in
    column *i* gets ``"c{i}n{k}"`` / ``"c{i}d{j}"``. Set an explicit ``id`` on a
    note/card to reference it from ``links``.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    summary_mode = note_style != "embed"

    # --- Title card, and optional index/overview hub, centred at the top. ---
    title_text = f"# {title}"
    if subtitle:
        title_text += f"\n\n{subtitle}"
    nodes.append({"id": "title", "type": "text", "text": title_text,
                  "x": -_TITLE_W // 2, "y": 0, "width": _TITLE_W, "height": _TITLE_H,
                  "color": "1"})

    anchor_id, anchor_bottom = "title", _TITLE_H
    if index_note:
        iy = _TITLE_H + 40
        stem = Path(index_note).stem
        if summary_mode:
            itext = _summary_card_text(stem, stem, index_summary)
            ih = _estimate_text_height(itext, _INDEX_W)
            nodes.append({"id": "index", "type": "text", "text": itext,
                          "x": -_INDEX_W // 2, "y": iy, "width": _INDEX_W,
                          "height": ih, "color": "1"})
            anchor_bottom = iy + ih
        else:
            nodes.append({"id": "index", "type": "file", "file": index_note,
                          "x": -_INDEX_W // 2, "y": iy, "width": _INDEX_W,
                          "height": _INDEX_H, "color": "1"})
            anchor_bottom = iy + _INDEX_H
        edges.append({"id": "e-title-index", "fromNode": "title",
                      "fromSide": "bottom", "toNode": "index", "toSide": "top"})
        anchor_id = "index"

    # --- Columns laid out left-to-right, all Group tops aligned. ---
    col_w = _ITEM_W + 2 * _PAD
    n = len(columns)
    total_w = n * col_w + (n - 1) * _COL_GAP if n else 0
    start_x = -total_w // 2
    cols_top = anchor_bottom + _TITLE_TO_COLS

    for i, col in enumerate(columns):
        gx = start_x + i * (col_w + _COL_GAP)
        color = col.get("color") if _is_valid_color(col.get("color")) else "0"
        item_x = gx + _PAD
        y = cols_top + _PAD
        lead_id: str | None = None
        prev_id: str | None = None  # for the vertical chain through the column

        for k, raw in enumerate(col.get("notes") or []):
            note = _norm_note(raw)
            stem = Path(note["file"]).stem
            nid = note["id"] or f"c{i}n{k}"
            if summary_mode:
                ntext = _summary_card_text(stem, note["title"], note["summary"])
                note_h = _estimate_text_height(ntext, _ITEM_W)
                nodes.append({"id": nid, "type": "text", "text": ntext,
                              "x": item_x, "y": y, "width": _ITEM_W, "height": note_h,
                              "color": color})
            else:
                note_h = _NOTE_H
                nodes.append({"id": nid, "type": "file", "file": note["file"],
                              "x": item_x, "y": y, "width": _ITEM_W, "height": note_h,
                              "color": color})
            if lead_id is None:
                lead_id = nid
                e: dict[str, Any] = {"id": f"e-anchor-{i}", "fromNode": anchor_id,
                                     "fromSide": "bottom", "toNode": nid, "toSide": "top"}
                if col.get("question"):
                    e["label"] = col["question"]
                edges.append(e)
            if prev_id is not None:
                edges.append({"id": f"e-chain-{i}-{k}", "fromNode": prev_id,
                              "fromSide": "bottom", "toNode": nid, "toSide": "top"})
            prev_id = nid
            y += note_h + _GAP_Y

        for j, raw in enumerate(col.get("cards") or []):
            text, given = _as_card(raw)
            cid = given or f"c{i}d{j}"
            card_h = _estimate_text_height(text, _ITEM_W)
            nodes.append({"id": cid, "type": "text", "text": text,
                          "x": item_x, "y": y, "width": _ITEM_W, "height": card_h,
                          "color": color})
            if prev_id is not None:
                edges.append({"id": f"e-chain-{i}-card{j}", "fromNode": prev_id,
                              "fromSide": "bottom", "toNode": cid, "toSide": "top"})
            elif lead_id is None:
                lead_id = cid
                edges.append({"id": f"e-anchor-{i}", "fromNode": anchor_id,
                              "fromSide": "bottom", "toNode": cid, "toSide": "top"})
            prev_id = cid
            y += card_h + _GAP_Y

        group_h = (y - _GAP_Y) - cols_top + _PAD
        group: dict[str, Any] = {"id": f"g{i}", "type": "group",
                                 "label": col.get("label", f"Section {i + 1}"),
                                 "x": gx, "y": cols_top, "width": col_w,
                                 "height": max(group_h, _PAD * 2)}
        if _is_valid_color(col.get("color")):
            group["color"] = col["color"]
        nodes.insert(0, group)  # behind its members

    # --- Extra caller-supplied cross-cluster links (loops, spines, syntheses). ---
    for m, link in enumerate(links or []):
        if "from" not in link or "to" not in link:
            continue
        e = {"id": link.get("id", f"x{m}"), "fromNode": link["from"],
             "fromSide": link.get("fromSide", "right"), "toNode": link["to"],
             "toSide": link.get("toSide", "left")}
        if link.get("label"):
            e["label"] = link["label"]
        if _is_valid_color(link.get("color")):
            e["color"] = link["color"]
        edges.append(e)

    return {"nodes": nodes, "edges": edges}


def validate_canvas_doc(doc: dict[str, Any], vault: Path) -> list[str]:
    """Return a list of human-readable problems with ``doc`` (empty = valid)."""
    problems: list[str] = []
    nodes = doc.get("nodes", [])
    ids: set[str] = set()
    for node in nodes:
        nid = node.get("id")
        if not nid:
            problems.append("a node is missing an 'id'")
            continue
        if nid in ids:
            problems.append(f"duplicate node id: {nid!r}")
        ids.add(nid)
        if node.get("color") is not None and not _is_valid_color(node["color"]):
            problems.append(f"node {nid!r} has invalid color {node['color']!r}")
        if node.get("type") == "file":
            rel = node.get("file", "")
            if not rel:
                problems.append(f"file node {nid!r} has no 'file' path")
            elif not (vault / rel).exists():
                problems.append(f"file node {nid!r} points at a missing note: {rel!r}")
    for edge in doc.get("edges", []):
        for end in ("fromNode", "toNode"):
            if edge.get(end) not in ids:
                problems.append(f"edge {edge.get('id', '?')!r} {end} -> unknown node {edge.get(end)!r}")
    return problems


def resolve_note_summaries(columns: list[dict[str, Any]], vault: Path) -> list[str]:
    """Fill each note's ``summary`` (from frontmatter when absent) in place.

    Normalises every note entry to a ``{file, id, title, summary}`` dict and, when
    no summary was supplied, reads it from the note's ``summary`` frontmatter.
    Returns the list of note paths that do not exist in the vault (so the caller
    can refuse to write a canvas with broken wikilinks).
    """
    missing: list[str] = []
    for col in columns:
        filled: list[dict[str, Any]] = []
        for raw in col.get("notes") or []:
            note = _norm_note(raw)
            if not note["file"] or not (vault / note["file"]).exists():
                missing.append(note["file"])
                continue
            if not note["summary"]:
                note["summary"] = _read_frontmatter_summary(vault / note["file"])
            filled.append(note)
        col["notes"] = filled
    return missing


def write_canvas_file(
    path: str,
    title: str,
    columns: list[dict[str, Any]],
    subtitle: str = "",
    index_note: str = "",
    links: list[dict[str, Any]] | None = None,
    note_style: str = "summary",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Resolve summaries, build, validate, and write a canvas. The tool body.

    Kept module-level (not a closure) so it can be unit-tested / driven directly,
    exercising the exact path the ``create_canvas`` tool runs.
    """
    try:
        target = resolve_in_vault(path)
    except ValueError as exc:
        return {"error": str(exc)}
    if target.suffix.lower() != ".canvas":
        return {"error": f"Canvas path must end in .canvas: {path!r}"}
    if target.exists() and not overwrite:
        return {"error": f"Canvas already exists: {path!r}. Pass overwrite=true to replace it.",
                "created": False}
    if not columns:
        return {"error": "Provide at least one column.", "created": False}

    vault = get_vault_path()
    missing = resolve_note_summaries(columns, vault)
    index_summary = ""
    if index_note:
        if not (vault / index_note).exists():
            missing.append(index_note)
        else:
            index_summary = _read_frontmatter_summary(vault / index_note)
    if missing:
        return {"error": "Some notes do not exist; nothing was written.",
                "missing_notes": missing, "created": False}

    doc = build_canvas_doc(title=title, columns=columns, subtitle=subtitle,
                           index_note=index_note, index_summary=index_summary,
                           links=links, note_style=note_style)
    problems = validate_canvas_doc(doc, vault)
    if problems:
        return {"error": "Canvas would be invalid; nothing was written.",
                "problems": problems, "created": False}

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        return {"error": f"Could not write canvas: {exc}", "created": False}

    groups = sum(1 for nd in doc["nodes"] if nd.get("type") == "group")
    note_count = sum(len(c.get("notes") or []) for c in columns)
    return {"created": True, "path": target.relative_to(vault).as_posix(),
            "note_style": note_style, "groups": groups, "notes": note_count,
            "total_nodes": len(doc["nodes"]), "edges": len(doc["edges"]),
            "hint": "Open it in Obsidian and zoom out (Shift+1) to see the whole topic at once."}


def register_canvas_tools(mcp: FastMCP) -> None:
    """Register the canvas tools on the given FastMCP instance."""

    @mcp.tool()
    def create_canvas(
        path: str,
        title: str,
        columns: list[dict[str, Any]],
        subtitle: str = "",
        index_note: str = "",
        links: list[dict[str, Any]] | None = None,
        note_style: str = "summary",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Generate a holistic Obsidian Canvas (`.canvas`) map of a topic.

        Hand this the notes of a course/topic grouped into **columns** (themed
        clusters) and it lays out a zoomed-out visual map: each cluster becomes a
        labelled, colour-coded Group region, and the hierarchy is wired with
        edges. The Group labels are what make the whole topic legible at a glance
        when zoomed out.

        By default each note is shown as a compact **essence card** — a clickable
        ``[[wikilink]]`` plus a one-line summary (taken from the note's ``summary``
        frontmatter, or from a ``summary`` you pass on the note) — so the map shows
        the *gist*, not a wall of full-note text. Use ``note_style="embed"`` to
        render full note file nodes instead.

        Args:
            path: Output path inside the vault, ending in ``.canvas``.
            title: Big title shown in the top card (the topic name).
            columns: Ordered list of cluster dicts. Each cluster::

                {
                  "label": "PART I · FIND MEANING",   # Group label (seen zoomed-out)
                  "color": "6",                        # "1".."6" or "#rrggbb"
                  "question": "Part I",                # optional edge label
                  "notes": [
                    "6 - Main Notes/.../Module 1 Intro.md",
                    {"file": ".../Module 2.md", "id": "m2", "summary": "override text"}
                  ],
                  "cards": ["## Key idea\\n\\nOne-card takeaway."]
                }

                A note's summary is auto-read from its frontmatter unless you pass
                one. Give a note/card an ``id`` to reference it from ``links``.
            subtitle: Optional second line under the title.
            index_note: Optional overview/index note shown as the hub under the
                title (also summarised, unless ``note_style="embed"``).
            links: Optional extra edges between nodes by id (cross-cluster links,
                a spine, loops). Each ``{"from","to","label","fromSide","toSide","color"}``.
            note_style: ``"summary"`` (default, essence cards) or ``"embed"``
                (full note file nodes).
            overwrite: If false (default), refuse to overwrite an existing canvas.

        Returns a summary with node/edge counts, or ``{"error": ...}`` (e.g. if a
        note path does not exist).
        """
        return write_canvas_file(path=path, title=title, columns=columns,
                                 subtitle=subtitle, index_note=index_note,
                                 links=links, note_style=note_style, overwrite=overwrite)

    @mcp.tool()
    def read_canvas(path: str) -> dict[str, Any]:
        """Read a `.canvas` file back as parsed JSON plus a short summary.

        ``read_note`` only accepts ``.md`` files, so this is how you inspect or
        iterate on an existing canvas.

        Args:
            path: Vault-relative path to a ``.canvas`` file.
        """
        try:
            target = resolve_in_vault(path)
        except ValueError as exc:
            return {"error": str(exc)}
        if not target.exists() or not target.is_file():
            return {"error": f"Canvas not found: {path!r}"}
        if target.suffix.lower() != ".canvas":
            return {"error": f"Not a .canvas file: {path!r}"}
        try:
            doc = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"error": f"Could not read canvas: {exc}"}
        nodes = doc.get("nodes", [])
        return {"path": target.relative_to(get_vault_path()).as_posix(),
                "groups": [n.get("label") for n in nodes if n.get("type") == "group"],
                "total_nodes": len(nodes), "edges": len(doc.get("edges", [])),
                "canvas": doc}
