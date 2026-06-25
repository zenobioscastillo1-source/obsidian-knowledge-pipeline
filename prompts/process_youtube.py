"""The `process-youtube` MCP prompt.

Returns a long instruction template that tells the agent how to drive the
YouTube + vault tools to turn a video into structured Obsidian notes (modules with SVG
diagrams, italic-wikilink tags, a per-source overview note, tag files, and a
persistent per-theme Obsidian Base index).

Implementation notes:

* FastMCP, not the low-level Server. Registered with ``@mcp.prompt()``; FastMCP
  derives the arguments from the function signature, and
  ``Annotated[..., Field(description=...)]`` carries their descriptions through.
* Substitution uses ``str.replace`` (not ``str.format``): the template is full of
  literal ``{``/``}`` and quotes (SVG, YAML, JSON-ish examples), which would break
  ``.format``. Only ``{url}`` / ``{theme}`` / ``{topic_name}`` / ``{target_folder}``
  are substituted.

Index design (per project decision):
* Processed notes go to ``6 - Main Notes``.
* The index is reimagined as **per-theme Obsidian Bases** in ``4 - Indexes``. Each
  note carries YAML frontmatter (``theme``, ``source``, ``type``, ``module``,
  ``summary``); a theme Base filters by ``theme``, groups rows by ``source``, and
  shows a ``summary`` column. A theme Base is created ONCE and persists — because
  it is a dynamic query, every future note tagged with that theme appears in it
  automatically, so it is never regenerated or overwritten.
* Concept tags still use the ``*tags:*`` italic-wikilink line (NOT frontmatter
  tags), preserving Linked Mentions + graph behaviour.
"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from prompts.voice import voice_step_text

PROCESS_YOUTUBE_PROMPT = """Process this YouTube video into structured Obsidian notes:

**Video URL:** {url}
**Theme:** {theme}
**Source / topic name:** {topic_name}
**Target folder:** {target_folder}
**Writing voice:** {voice}

Follow this pipeline exactly:

---

{voice_step}

---

## Step 1: Extract content

1. Call `get_youtube_metadata` with url="{url}" to get the video title and channel.
2. Call `get_youtube_transcript` with url="{url}" to get the full transcript.

If the transcript fails, tell me and stop — I may need to paste it manually.

**Optional — capture real visuals (for slide/chart/diagram-heavy videos):** if the
video clearly shows charts, frameworks, slides, or on-screen code that the words
alone don't capture, call `get_youtube_frames` with url="{url}" (default scene mode).
Use the returned frames to (a) make the module rewrites accurate to what was on
screen, (b) ground the SVG diagrams in the real visuals shown, and (c) embed the
most instructive saved frames into the matching module using the `embed` snippet
each frame returns — replace its caption placeholder with a one-line explanation of
the point it illustrates. Skip this for talking-head/text-only videos to save cost
(it downloads the video and needs ffmpeg installed).

---

## Step 2: Check existing vault state

3. Call `search_vault` with folder="3 - Tags" and search_content=false to get existing tags (prevents duplicate tag files).
4. Call `list_folder` on `4 - Indexes` to see which **theme Bases** (`.base` files) already exist.
5. Call `list_folder` on `6 - Main Notes/{target_folder}` to check for filename collisions.

**Resolve the theme now:** if a theme was provided ("{theme}"), use it. Otherwise, infer a short theme from the video's content (e.g. "AI", "Trading", "Self-Development"). Either way, if an existing theme Base in `4 - Indexes` clearly matches, REUSE its exact theme label and filename rather than inventing a near-duplicate.

---

## Step 3: Analyze the transcript

Analyze the full transcript and identify:

**Module boundaries:** YouTube transcripts lack headers, so identify 3-7 natural topic shifts based on content. Each module should cover one coherent topic (roughly 3-8 minutes of video content). Name them "Module 1: [Descriptive Title]", "Module 2: [Descriptive Title]", etc.

**A one-line summary per module:** a short description of what that module covers — this becomes the module's `summary` frontmatter property and the "What it covers" column in the theme Base.

**Tag concepts:** Extract 4-8 relevant concepts per module for the tags line. Check these against the existing tags from Step 2 — only create NEW tag files for concepts that don't already exist in the vault.

**Table opportunities:** Find data that belongs in tables — comparisons, timelines, lists of findings, key definitions, step-by-step processes.

**SVG diagram opportunities:** For every module, identify concepts that benefit from a visual:
- Multi-step processes or flows
- Hierarchies or stack diagrams
- Hub-and-spoke frameworks (central concept + satellite nodes)
- Journey or timeline diagrams
- Side-by-side comparisons or mappings
- Concept maps with 3+ interrelated components

Every module with at least one visual concept gets an SVG diagram.

---

## Step 4: Generate module files

For each module, generate a markdown file following this EXACT structure. Note the **YAML frontmatter** at the very top — it powers the theme Base.

```
---
theme: {theme}
source: {topic_name}
type: module
module: N
summary: One line describing what this module covers
---

# Module N: [Descriptive Title]

## Tags

*tags:* [[Tag One]] [[Tag Two]] [[Tag Three]] [[Tag Four]]

---

> [!info] Visual: [Diagram Title]
<svg viewBox="0 0 680 [HEIGHT]" xmlns="http://www.w3.org/2000/svg">
  [Full self-contained SVG — see SVG rules below]
</svg>
> [1-2 sentences explaining what the diagram shows]

## [Section Title]

[Beginner-friendly rewrite of the content. CRITICAL RULES:
- Write in your OWN words — never copy-paste from the transcript
- ELI15 level — assume reader encounters every concept for the first time
- Bold technical terms and define them inline: **Order flow** (the real-time stream of buy and sell orders hitting the market)
- Use tables for any structured/comparison data
- Use analogies and real-world examples to explain abstract concepts
- Write the prose in the voice resolved in Step 0 (the user's own voice if chosen, otherwise the house style) — the voice shapes tone/phrasing/rhythm only, never the structure above]

## [Next Section Title]

[Continue...]

---

## Navigation

<- **Previous:** [[Module N-1 Descriptive Title]] | **Next:** [[Module N+1 Descriptive Title]] ->

**Back to Overview:** [[{topic_name} — Overview]]

---

[^1]: {url}
```

FIRST module has no Previous line. LAST module has no Next line. Write each module to `6 - Main Notes/{target_folder}/`.

---

## Step 5: Generate the overview note + ensure the theme Base

### 5a. Per-source overview note → `6 - Main Notes/{target_folder}/{topic_name} — Overview.md`

```
---
theme: {theme}
source: {topic_name}
type: overview
summary: One line describing this whole source
---

# {topic_name} — Overview

**Source:** [Channel Name] YouTube Video
**Original Title:** [Video Title]
**URL:** {url}

## Tags

*tags:* [[Tag One]] [[Tag Two]] [[Tag Three]] [[Tag Four]] [[Tag Five]]

## Course Overview

[2-3 paragraph beginner-friendly summary of the entire video's content.
What will the reader learn? Why does it matter? Who is this for?]

## In This Theme

![[<ThemeFile>.base]]

(The table above is the live theme Base — every source under this theme, grouped, with each module's summary. Do NOT hand-maintain a module table here.)

## Key Ideas at a Glance

- Main insight from the video
- Critical concept or framework
- Practical application or next step
- Important distinction the video makes

---

## Quick Navigation

**Start Here:** [[Module 1 Descriptive Title]]

---

[^1]: {url}
```

### 5b. Theme Base → `4 - Indexes/<ThemeFile>.base`  (create ONLY if it does not already exist)

`<ThemeFile>` is the theme made filesystem-safe — replace any of `/ \\ : * ? " < > |` with a space or hyphen (e.g. theme `AI / Claude` → file `AI - Claude.base`, embedded as `![[AI - Claude.base]]`). The frontmatter `theme:` value and the filter below keep the human-readable theme.

**If a Base for this theme already exists (from Step 2), DO NOT recreate or overwrite it** — it is a dynamic query and your new notes will appear in it automatically. Only create it when it is missing, using this exact structure:

```
filters:
  and:
    - 'theme == "{theme}"'
    - 'type == "module"'
properties:
  note.source:
    displayName: Source
  note.module:
    displayName: "#"
  note.summary:
    displayName: What it covers
views:
  - type: table
    name: {theme}
    groupBy:
      property: note.source
      direction: ASC
    order:
      - note.source
      - note.module
      - file.name
      - note.summary
```

This collects every module across all sources under the theme, **grouped by source title**, with a column describing what each module covers.

Rules for the `.base` file:
- It is YAML configuration, NOT a note: no `[[wikilinks]]`, no `*tags:*`, no prose.
- Indent with two spaces; wrap filter expressions in single quotes as shown.
- The filename must be filesystem-safe and must match the embed in the overview note.

---

## Step 6: Generate tag files

For each tag concept that does NOT already exist in `3 - Tags/`, create a file:

**Filename:** `3 - Tags/[Tag Name].md`
**Content:**
```
*[One-line description of this concept and why it matters.]*

> [!info] Scroll to **Linked Mentions** below to see every note that references this concept.
```

Tag files are intentionally minimal — Obsidian's Linked Mentions does the indexing automatically.

DO NOT create a tag file if a file with that name already exists in `3 - Tags/`.

---

## Step 7: Write everything to the vault

Use `create_note` for each file, in this order:

1. All module files → `6 - Main Notes/{target_folder}/`
2. Overview note → `6 - Main Notes/{target_folder}/{topic_name} — Overview.md`
3. Theme Base → `4 - Indexes/<ThemeFile>.base` — **only if it does not already exist** (never overwrite an existing theme Base)
4. New tag files → `3 - Tags/`

---

## SVG Rules for Obsidian

Place each SVG directly under a `> [!info] Visual: …` callout title line. The `<svg>…</svg>` lines must have **NO** `> ` prefix (and leave no blank `> ` line between the title and `<svg>`) — if the SVG lines are prefixed with `> `, Obsidian shows the raw source instead of rendering the image. Put the `> ` prefix only on the 1–2 sentence caption that follows `</svg>`, so the caption still sits in the callout.

**Design system:**
- viewBox="0 0 680 [HEIGHT]" — set height to fit content + 40px padding
- Safe content area: x=40 to x=640, y=40 to y=(H-40)
- Font: set `font-family` on EVERY `<text>` element — do NOT rely on inheritance from the root `<svg>`, or Obsidian's theme CSS overrides it and text falls back to a serif font. Use `font-family="Anthropic Sans, -apple-system, sans-serif"` on each `<text>`.
- Title text: 14px, font-weight 500 | Body/subtitle text: 12px, font-weight 400
- Put `dominant-baseline="central"` on `<text>` for reliable vertical centering, and `text-anchor="middle"` for horizontally centered labels (so `x`/`y` are the text's center)
- All rects: rx="8", stroke-width="0.5"
- No <style> blocks — put presentation attributes (font-family, font-size, fill, …) inline on every element
- No text below 12px, no external references
- Fully self-contained (no CSS classes, no external anything)

**Color palette (dark background panels, bright borders, light text):**
- Blue (info, structure): fill="rgb(12,68,124)" stroke="rgb(133,183,235)" text fill="rgb(181,212,244)"
- Teal (growth, positive): fill="rgb(8,80,65)" stroke="rgb(93,202,165)" text fill="rgb(159,225,203)"
- Amber (caution, transition): fill="rgb(99,56,6)" stroke="rgb(239,159,39)" text fill="rgb(250,199,117)"
- Green (success, action): fill="rgb(39,80,10)" stroke="rgb(151,196,89)" text fill="rgb(192,221,151)"
- Coral (warning, problem): fill="rgb(113,43,19)" stroke="rgb(240,153,123)" text fill="rgb(245,196,179)"
- Purple (core, synthesis): fill="rgb(60,52,137)" stroke="rgb(175,169,236)" text fill="rgb(206,203,246)"

**Arrow marker (add to <defs> when arrows are needed):**
```xml
<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
  <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</marker>
```

**Connector lines:** stroke="rgb(156,154,146)" stroke-width="1.5" opacity="0.4" marker-end="url(#arrow)"

**Diagram patterns to use:**
- Stack/Hierarchy: Rows stacked top-to-bottom for levels, stages, difficulty
- Hub and Spoke: Central circle + 4 satellite rects for frameworks
- Journey Path: Left-to-right path with labeled anchor points for processes
- Mapping Table: Two columns with arrow connectors for comparisons

---

## CRITICAL RULES

- Filenames use SPACES, never underscores
- All cross-references use [[Wikilink]] format
- Every note begins with YAML frontmatter (`theme`, `source`, `type`, plus `module` for modules, and `summary`) — this powers the theme Base. Quote any frontmatter value that contains a colon or special character.
- Concept tags use the *tags:* [[Tag Name]] italic-wikilink line, NOT #hashtags and NOT a frontmatter `tags:` field
- Every module has navigation (previous/next/back to overview)
- Every file has [^1] footnote citation to the source URL
- The `<svg>…</svg>` lines have NO `> ` prefix (so Obsidian renders the image); only the `> [!info]` title above and the caption sentence below the SVG carry `> `
- Never create duplicate tag files
- Minimum 1 SVG diagram per module that has a visual concept
- Beginner-friendly tone throughout (ELI15 level)
- Never copy-paste from transcript — always rewrite in your own words
- Write all prose in the voice resolved in Step 0; the user's voice (when chosen) governs tone, phrasing, and rhythm ONLY — never the structure (frontmatter, *tags:*, tables, SVGs, ELI15 clarity, navigation, footnotes are always produced as specified)
- The theme Base in `4 - Indexes` is PER-THEME and PERSISTENT — create it only if missing, never overwrite it; it auto-collects new notes via its `theme` filter
- `.base` files are YAML config only — never put [[wikilinks]], *tags:*, prose, or footnotes inside them
"""


def register_youtube_prompt(mcp: FastMCP) -> None:
    """Register the `process-youtube` prompt on the given FastMCP instance."""

    @mcp.prompt(
        name="process-youtube",
        description=(
            "Process a YouTube video into structured Obsidian notes (modules with "
            "SVG diagrams, tags) in 6 - Main Notes, indexed by a per-theme Obsidian Base."
        ),
    )
    def process_youtube(
        url: Annotated[str, Field(description="YouTube video URL")],
        theme: Annotated[
            str,
            Field(description="Theme that groups this note's Base (e.g. 'AI', 'Trading'). Inferred from the video if omitted."),
        ] = "",
        topic_name: Annotated[
            str,
            Field(description="Source/course title (used as the 'source' property and overview note name); derived from the video title if omitted)"),
        ] = "",
        target_folder: Annotated[
            str,
            Field(description="Subfolder within 6 - Main Notes (defaults to the source title if omitted)"),
        ] = "",
        voice: Annotated[
            str,
            Field(
                description=(
                    "Writing voice: 'mine' to write in the user's own voice (see "
                    "the analyze-voice prompt), 'default' for the pipeline's house "
                    "style. Leave blank to be asked."
                )
            ),
        ] = "",
    ) -> str:
        return (
            # The voice step is inserted first so its own {voice} token is then
            # filled by the .replace below (along with the header line).
            PROCESS_YOUTUBE_PROMPT.replace("{voice_step}", voice_step_text())
            .replace("{url}", url)
            .replace(
                "{theme}",
                theme or "[infer a short theme from the video; reuse an existing theme Base in 4 - Indexes if one fits]",
            )
            .replace("{topic_name}", topic_name or "[derive from video title]")
            .replace(
                "{target_folder}",
                target_folder or "[use the source title as the subfolder name]",
            )
            .replace(
                "{voice}",
                voice
                or "[not specified — ASK the user (see Step 0) whether to use their own voice or the default house style]",
            )
        )
