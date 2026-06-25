"""The `build-canvas` MCP prompt.

Encodes the conventions for turning an existing course/topic folder into a
holistic, zoomed-out Obsidian Canvas map via the ``create_canvas`` tool. Keeps
the *intelligence* (how to cluster the notes, what to call each region) in the
model and the *layout maths* in the tool — the same split the rest of the
pipeline uses.

For large folders it describes a **map-reduce** flow: cheap reader subagents
(``reader_model``, default Sonnet) each distil a batch of notes in parallel and
return structured extracts; the orchestrator (the strong model you're already
running) then clusters, colours, writes the spine, and calls ``create_canvas``.
The MCP itself still makes no LLM calls — the fan-out is driven by the host's
Agent tool, so portability and the no-AI scripting path are preserved.
"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

BUILD_CANVAS_PROMPT = """Build a holistic Canvas map of an existing topic folder so the whole subject can be understood in one zoomed-out view.

**Target folder:** {target_folder}
**Reader model (for the fan-out):** {reader_model}

## Step 1: Read the structure (you, the orchestrator)
1. `list_folder` on `{target_folder}` to see every note, and **count the module notes**.
2. `read_note` on the index/overview note — YOU always read this yourself; it frames the whole clustering (the source's parts, arc, order).

## Step 2: Get an essence + extract for every module note

Each module becomes an **essence card**: a clickable `[[wikilink]]` title plus a faithful summary of the note's *real essence* (NOT the full note) — a rich paragraph **up to ~300 words** covering the core argument, key terms, and takeaway. Cards **auto-size** to length. A note's one-line `summary` frontmatter is too thin for this, so an essence must be *written from the note's content*.

Choose ONE path by size:

**A. Small folder (≤ ~25 notes): read inline.** Just `read_note` each module yourself and write its essence. Fan-out overhead isn't worth it at this scale.

**B. Large folder (> ~25 notes, or you want speed): fan out reader subagents (map step).**
- Split the module notes into **batches of ~4–6** and spawn one **Agent** per batch, **in parallel** (multiple Agent calls in a single message), with `subagent_type: "general-purpose"` and `model: "{reader_model}"`.
- Give each subagent this job: *"Read each of these notes (use the Read tool on the absolute path `D:/Obsidian/Talambuhay/<vault-relative path>`, or `read_note`). For EACH note return a compact record — `file` (vault-relative path), `title`, 6–10 load-bearing points, key terms with one-line definitions, the central diagram/visual idea, and a faithful **draft essence of ~120–180 words** in plain prose. Stay faithful to the author's framing and terminology; do not invent. Return all records as one structured block."*
- The subagents return extracts; **you never have to read every module yourself.**

**On the reader model:** default is **Sonnet** — it keeps the nuance an essence needs (key terms, the author's framing). **Haiku** is faster and fine when the notes are short/simple or when you'll tighten every essence yourself in the reduce step; just expect to do more polishing. Pick per the trade-off you want.

## Step 3: Cluster, polish, assemble (reduce step — you, the orchestrator)
Using the index structure (Step 1) and the extracts (Step 2):
1. **Cluster** the notes into **3–5 columns** that follow the source's own divisions (Part I/II/III, a begin→middle→end arc). If the book has three parts, use three columns.
2. For each column decide: a short ALL-CAPS **label**, a **color** ("1" red, "2" orange, "3" yellow, "4" green, "5" cyan, "6" purple — adjacent columns differ), an optional **question** (hub edge label), and the **notes** in reading order. Give each column's first note a stable **id** for the spine.
3. **Polish each draft essence** into the final `summary` (tighten, keep it faithful, consistent voice, ≤ ~300 words). Subagent drafts are raw material — you own the final wording.

## Step 4: Call create_canvas
- `path`: `{target_folder}/<Topic> Map.canvas`
- `title` (+ `subtitle` with source/author and a "left → right" hint)
- `index_note`: the overview/index note (the hub the columns hang from)
- `columns`: each `{{"label","color","question","notes":[{{"file","id","summary"}} ...]}}`
- `links`: a spine connecting each column's lead id to the next (`{{"from","to","label":"then …","color":"1"}}`), plus any genuine cross-cluster links.

## Rules
- Every note path must exist (the tool refuses otherwise) and every essence must be faithful to its note.
- The map is a structural overview, not a copy of the notes — essences distil, they don't transcribe.
- After writing, tell me to open it in Obsidian and zoom out (Shift+1) to see the whole topic at once.
"""


def register_canvas_prompt(mcp: FastMCP) -> None:
    """Register the `build-canvas` prompt on the given FastMCP instance."""

    @mcp.prompt(
        name="build-canvas",
        description=(
            "Turn an existing course/topic folder into a holistic, zoomed-out "
            "Obsidian Canvas map (colour-coded Group regions) via create_canvas. "
            "Fans out reader subagents (map-reduce) for large folders."
        ),
    )
    def build_canvas(
        target_folder: Annotated[
            str,
            Field(description="Folder under 6 - Main Notes to map, e.g. '6 - Main Notes/The Art of Focus'"),
        ],
        reader_model: Annotated[
            str,
            Field(description="Model for the fan-out reader subagents: 'sonnet' (default, keeps nuance) or 'haiku' (fastest, more polishing needed). Only used for large folders."),
        ] = "sonnet",
    ) -> str:
        return (
            BUILD_CANVAS_PROMPT
            .replace("{target_folder}", target_folder)
            .replace("{reader_model}", reader_model or "sonnet")
        )
