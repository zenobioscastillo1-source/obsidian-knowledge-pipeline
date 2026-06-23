# Obsidian Knowledge Pipeline — MCP Server Spec

## Project overview

An MCP server that gives Claude (via Claude Code or Claude Desktop) the ability to:

1. **Extract** content from YouTube videos (transcripts + metadata)
2. **Read/write** an Obsidian vault on the local filesystem
3. **Process** content into structured notes using the obsidian-content-processor style

The server is the bridge between Claude and the user's vault. Claude orchestrates — the server provides the hands.

**v1 scope:** YouTube → Obsidian only. Articles, PDFs, EPUBs are future iterations.

---

## Tech stack

- **Language:** Python 3.11+
- **MCP SDK:** `mcp` (Python SDK)
- **YouTube transcripts:** `youtube-transcript-api`
- **HTTP requests:** `httpx` (for YouTube metadata via oEmbed)
- **Transport:** stdio (works with both Claude Code and Claude Desktop)
- **Package manager:** `uv` (recommended) or `pip`

---

## Project structure

```
obsidian-knowledge-pipeline/
├── server.py              # MCP server — all tools, prompts, resources
├── tools/
│   ├── __init__.py
│   ├── youtube.py         # get_youtube_transcript, get_youtube_metadata
│   └── vault.py           # search_vault, read_note, create_note, list_folder
├── prompts/
│   ├── __init__.py
│   └── process_youtube.py # The obsidian-content-processor prompt template
├── config.py              # Vault path configuration
├── .env                   # VAULT_PATH=/path/to/obsidian/vault
├── requirements.txt       # mcp, youtube-transcript-api, httpx, python-dotenv
└── README.md
```

---

## Configuration

The server needs one environment variable:

```env
VAULT_PATH=/Users/zenobios/Documents/My Second Brain
```

`config.py` loads it and validates the path exists. All vault tools resolve paths relative to `VAULT_PATH`.

---

## Tool definitions

### Tool 1: `get_youtube_transcript`

**Purpose:** Extract the full transcript from a YouTube video.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `url` | string | yes | YouTube video URL (any format — full, short, embed) |
| `language` | string | no | Preferred language code (default: `en`) |

**Returns:**

```json
{
  "video_id": "dQw4w9WgXcQ",
  "segments": [
    { "text": "Never gonna give you up", "start": 0.0, "duration": 3.2 },
    { "text": "Never gonna let you down", "start": 3.2, "duration": 2.8 }
  ],
  "full_text": "Never gonna give you up Never gonna let you down...",
  "language": "en"
}
```

**Implementation notes:**
- Parse video ID from URL using regex (handle youtube.com/watch?v=, youtu.be/, youtube.com/embed/)
- Use `youtube_transcript_api.YouTubeTranscriptApi.get_transcript(video_id)`
- Fall back to auto-generated captions if manual captions unavailable
- Return both segmented (with timestamps) and concatenated full text

---

### Tool 2: `get_youtube_metadata`

**Purpose:** Fetch video title, channel, duration, and description.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `url` | string | yes | YouTube video URL |

**Returns:**

```json
{
  "title": "How to Trade Gold Like a Pro",
  "author_name": "Fabio Valentini",
  "author_url": "https://www.youtube.com/@FabioValentini",
  "thumbnail_url": "https://i.ytimg.com/vi/xxx/maxresdefault.jpg"
}
```

**Implementation notes:**
- Use YouTube oEmbed endpoint: `https://www.youtube.com/oembed?url={url}&format=json`
- This requires no API key — it's a public endpoint
- Returns title, author_name, author_url, thumbnail_url

---

### Tool 3: `search_vault`

**Purpose:** Search for notes in the vault by filename or content. Critical for tag deduplication.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | string | yes | Search term (checked against filenames and file content) |
| `folder` | string | no | Restrict search to a specific folder (e.g., `3 - Tags`) |
| `search_content` | boolean | no | Also search inside file content, not just filenames (default: `false`) |
| `max_results` | integer | no | Max results to return (default: `10`) |

**Returns:**

```json
{
  "results": [
    {
      "path": "3 - Tags/Auction Market Theory.md",
      "filename": "Auction Market Theory.md",
      "folder": "3 - Tags",
      "snippet": "The slip-box method: atomic, linked, evergreen notes..."
    }
  ],
  "total_found": 1
}
```

**Implementation notes:**
- Case-insensitive filename matching using `fnmatch` or simple substring
- If `search_content` is true, read file contents and search (limit to .md files)
- Return first 200 chars as snippet when content-searching
- Respect `.obsidian` and other hidden folders — never search those

---

### Tool 4: `read_note`

**Purpose:** Read the full content of a specific note.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | string | yes | Relative path from vault root (e.g., `6 - Main Notes/Trading/What Is Price Action.md`) |

**Returns:**

```json
{
  "path": "6 - Main Notes/Trading/What Is Price Action.md",
  "content": "# What Is Price Action\n\n## Tags\n\n*tags:* [[Trading]] [[Price Action]]...",
  "size_bytes": 3420,
  "last_modified": "2026-05-28T14:30:00"
}
```

**Implementation notes:**
- Resolve path relative to `VAULT_PATH`
- Validate the file exists and is a `.md` file
- Return content as UTF-8 string
- Include file size and last modified timestamp

---

### Tool 5: `create_note`

**Purpose:** Create a new note in the vault with content.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | string | yes | Relative path including filename (e.g., `2 - Source Material/Module 1 Introduction.md`) |
| `content` | string | yes | Full markdown content of the note |
| `overwrite` | boolean | no | Overwrite if file exists (default: `false`) |

**Returns:**

```json
{
  "path": "2 - Source Material/Module 1 Introduction.md",
  "created": true,
  "size_bytes": 5120
}
```

**Implementation notes:**
- Resolve path relative to `VAULT_PATH`
- Create intermediate directories if they don't exist (e.g., `2 - Source Material/Trading Basics/`)
- Refuse to overwrite by default — return error if file exists and `overwrite` is false
- Validate filename uses spaces (not underscores) per vault convention
- Write content as UTF-8

---

### Tool 6: `list_folder`

**Purpose:** List files and subfolders in a vault directory.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `path` | string | no | Relative folder path (default: vault root) |
| `recursive` | boolean | no | List all nested contents (default: `false`) |

**Returns:**

```json
{
  "path": "3 - Tags",
  "items": [
    { "name": "Auction Market Theory.md", "type": "file", "size_bytes": 245 },
    { "name": "Knowledge Management.md", "type": "file", "size_bytes": 312 },
    { "name": "Trading", "type": "folder", "item_count": 8 }
  ],
  "total_files": 2,
  "total_folders": 1
}
```

**Implementation notes:**
- Resolve path relative to `VAULT_PATH`
- Skip hidden files/folders (starting with `.`)
- For folders, include item count
- Sort: folders first, then files alphabetically

---

## MCP Prompt: `process-youtube`

This is the core intelligence — the obsidian-content-processor logic packaged as an MCP prompt template.

> **Design update (2026-06-03) — supersedes the template printed below.** The authoritative
> prompt now lives in `prompts/process_youtube.py`. Three changes were made by user decision:
> 1. **Processed notes go to `6 - Main Notes/<target_folder>/`** (not `2 - Source Material`).
> 2. **Every note carries YAML frontmatter** (`theme`, `source`, `type`, `module`, `summary`).
>    Concept tags still use the `*tags:*` italic-wikilink line, not a frontmatter `tags:` field.
> 3. **The index is reimagined as per-theme Obsidian Bases.** Instead of a per-video markdown
>    MOC, `4 - Indexes` holds one `.base` per theme (e.g. `AI.base`) that filters `theme == "…"`,
>    groups rows by `source`, and shows each module's `summary`. A theme Base is created once and
>    persists — as a dynamic query it auto-collects every future note tagged with that theme. A new
>    `theme` argument (inferred when blank) selects which Base a video joins. A per-source
>    `<Source> — Overview.md` note (also in `6 - Main Notes`) keeps the prose overview + key ideas.

**Prompt name:** `process-youtube`

**Arguments:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `url` | string | yes | YouTube video URL to process |
| `topic_name` | string | no | Override topic name for the index (otherwise derived from video title) |
| `target_folder` | string | no | Subfolder within `2 - Source Material` (default: none — files go directly in `2 - Source Material`) |

**The prompt returns a messages array that instructs Claude to:**

1. Call `get_youtube_metadata` to get video title, channel, URL
2. Call `get_youtube_transcript` to get the full transcript
3. Call `search_vault` in `3 - Tags` folder to check existing tags (for deduplication)
4. Call `list_folder` on the target folder to avoid filename collisions
5. Analyze the transcript for module boundaries (topic shifts every 3-8 minutes of content)
6. For each module, generate:
   - Beginner-friendly rewrite in own words
   - Inline technical term explanations
   - Tables for structured/comparison data
   - SVG illustrations embedded in Obsidian callout blocks
   - `*tags:*` line with `[[wikilinked]]` concepts
   - Navigation links (previous/next module + back to index)
   - Footnote citation to source video
7. Generate the index file with course overview, module table, and key ideas
8. Generate tag files for any NEW concepts not already in `3 - Tags`
9. Call `create_note` for each file:
   - Modules → `2 - Source Material/[target_folder]/`
   - Index → `4 - Indexes/`
   - Tags → `3 - Tags/`

**Full prompt template content** (this is what gets returned when a client calls `get_prompt("process-youtube")`):

```
You are processing a YouTube video into structured Obsidian notes. Use the
MCP tools available to you to complete this pipeline.

## Step 1: Extract

Call get_youtube_metadata with the provided URL.
Call get_youtube_transcript with the provided URL.

## Step 2: Scan existing vault

Call search_vault with folder="3 - Tags" to check which tags already exist.
Call list_folder on the target output folder to check for filename collisions.

## Step 3: Analyze content

Analyze the transcript and identify:
- Module boundaries: Look for topic shifts. Each module should cover one
  coherent topic (roughly 3-8 minutes of video content). YouTube transcripts
  lack headers, so identify 3-7 natural topic shifts.
- Key concepts for tags (4-8 per module)
- Data that belongs in tables (comparisons, lists, step-by-step processes)
- Concepts that benefit from SVG diagrams (processes, hierarchies, frameworks,
  comparisons with 3+ items, cycles)

## Step 4: Generate module files

For each module, create a markdown file following this exact structure:

---
# Module N: [Descriptive Title]

## Tags

*tags:* [[Tag One]] [[Tag Two]] [[Tag Three]] [[Tag Four]]

---

> [!info] Visual: [Diagram Title]
>
> <svg viewBox="0 0 680 [HEIGHT]" xmlns="http://www.w3.org/2000/svg">
>   [Self-contained SVG with inline styles, no external refs]
>   [Font: "Anthropic Sans", -apple-system, sans-serif]
>   [Title text: 14px weight 500 | Body text: 12px weight 400]
>   [Dark background panels with bright border strokes and light text]
>   [Color palette — Blue: fill rgb(12,68,124) border rgb(133,183,235) text rgb(181,212,244)]
>   [Teal: fill rgb(8,80,65) border rgb(93,202,165) text rgb(159,225,203)]
>   [Amber: fill rgb(99,56,6) border rgb(239,159,39) text rgb(250,199,117)]
>   [All rects: rx="8" stroke-width="0.5"]
>   [Every line of SVG must start with > for Obsidian callout rendering]
> </svg>

[1-2 sentences explaining what the diagram shows]

## [Section Title]

[Beginner-friendly rewrite — ELI15 level. Never copy-paste from transcript.
Rephrase everything in your own words.]

**Technical term:** (Simple definition in parentheses)

| Column | Column |
|--------|--------|
| Data   | Data   |

[Continue for each section within this module's scope]

---

## Navigation

<- **Previous:** [[Module N-1 Title]] | **Next:** [[Module N+1 Title]] ->

**Back to Index:** [[Topic - Full Course Index]]

---

[^1]: [YouTube video URL]
---

## Step 5: Generate index file

Create the index following this structure:

---
# [Topic] — Full Course Index

**Source:** [Channel Name] YouTube Video [Date if available]
**Original Title:** [Video Title]

## Tags

*tags:* [[Tag One]] [[Tag Two]] [[Tag Three]] [[Tag Four]] [[Tag Five]]

## Course Overview

[2-3 paragraph beginner-friendly summary of what the video covers]

## Modules

| Module | Topic | What You'll Learn |
|--------|-------|-------------------|
| [[Module 1 Title]] | Brief topic | Key takeaway |
| [[Module 2 Title]] | Brief topic | Key takeaway |

## Key Ideas at a Glance

- Main insight
- Critical concept
- Practical application

---

## Quick Navigation

**Start Here:** [[Module 1 Title]]

---

[^1]: [YouTube video URL]
---

## Step 6: Generate tag files

For each tag concept that does NOT already exist in 3 - Tags:

---
*[One-line description of the concept and its relevance.]*

> [!info] Scroll to **Linked Mentions** below to see every note that
> references this concept.
---

## Step 7: Write to vault

Use create_note for each file:
- Module files → 2 - Source Material/[target_folder if specified]/
- Index file → 4 - Indexes/
- New tag files → 3 - Tags/

CRITICAL RULES:
- Filenames use SPACES, never underscores
- All cross-references use [[Wikilink]] format
- Tags use *tags:* [[Tag]] format (italic wikilinks), NOT #hashtags
- Every module has navigation (previous/next/index)
- First module has no Previous; last module has no Next
- Every file has [^1] footnote citation
- SVG callout lines must ALL start with > (including blank lines)
- Never create a tag file if that tag already exists in the vault
- Minimum 1 SVG diagram per module that has a visual concept
```

---

## MCP Resource: `vault-structure`

An optional resource that exposes the vault's folder structure so clients can understand the layout without calling `list_folder` first.

**URI:** `vault://structure`

**Returns:** A text description of the 6-folder system and what goes where:

```
Vault: obsidian-knowledge-system
Structure: Zettelkasten-inspired, 6 numbered folders

1 - Rough notes/     → Fleeting captures, inbox (temporary)
2 - Source Material/  → Literature notes from videos, articles, books
3 - Tags/            → One concept per file, auto-indexed via Linked Mentions
4 - Indexes/         → Per-theme Obsidian Bases (.base) — dynamic, grouped by source
5 - Templates/       → Reusable note scaffolds
6 - Main Notes/      → Processed pipeline notes land here (modules + per-source overview)

Conventions:
- Filenames use spaces, never underscores
- Tags: *tags:* [[Concept Name]] (italic wikilinks)
- Cross-references: [[Note Title]] wikilinks
- Graph colors are path-driven (folder determines color)
- Tag files are intentionally near-empty (Linked Mentions does the work)
```

---

## Implementation phases

### Phase 1: Skeleton + vault tools (build first)

Get the MCP server running with vault read/write tools only. This is testable immediately with the MCP Inspector.

**Files to create:**
- `server.py` — MCP server initialization, tool registration
- `tools/vault.py` — `search_vault`, `read_note`, `create_note`, `list_folder`
- `config.py` — load VAULT_PATH from .env
- `.env` — vault path
- `requirements.txt`

**Test with:** `mcp dev server.py` → open Inspector → test each vault tool

### Phase 2: YouTube extraction tools

Add the source extraction layer.

**Files to create/modify:**
- `tools/youtube.py` — `get_youtube_transcript`, `get_youtube_metadata`
- `server.py` — register new tools

**Test with:** Inspector → call `get_youtube_transcript` with a real URL

### Phase 3: Process-YouTube prompt

Add the prompt template that ties everything together.

**Files to create/modify:**
- `prompts/process_youtube.py` — the full prompt template
- `server.py` — register prompt

**Test with:** Connect to Claude Code or Claude Desktop → ask it to process a YouTube video

### Phase 4: Connect to Claude Code + Claude Desktop

Configure the server in both clients.

**Claude Code (`~/.claude.json` or project `.mcp.json`):**
```json
{
  "mcpServers": {
    "obsidian-knowledge-pipeline": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/obsidian-knowledge-pipeline", "server.py"],
      "env": {
        "VAULT_PATH": "/path/to/vault"
      }
    }
  }
}
```

**Claude Desktop (`claude_desktop_config.json`):**
```json
{
  "mcpServers": {
    "obsidian-knowledge-pipeline": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/obsidian-knowledge-pipeline", "server.py"],
      "env": {
        "VAULT_PATH": "/path/to/vault"
      }
    }
  }
}
```

---

## Security considerations

- The server has full read/write access to the vault folder — and ONLY that folder
- All paths are resolved relative to `VAULT_PATH` and validated to prevent directory traversal (no `../` escaping)
- No API keys needed for v1 (YouTube oEmbed + transcript API are both keyless)
- The `.env` file is gitignored

---

## Future iterations (post-v1)

- **Video frame extraction:** Add `get_youtube_frames` so Claude can "watch" a video — sample keyframes and reason over them alongside the transcript. See the detailed draft in [Proposed feature: `get_youtube_frames`](#proposed-feature-get_youtube_frames--video-frame-extraction-candidate-phase-5) below.
- **Article extraction:** Add `get_article_content` tool using `trafilatura` or `readability`
- **PDF extraction:** Add `get_pdf_content` tool using `pymupdf` or `pdfplumber`
- **EPUB extraction:** Add `get_epub_content` tool using `ebooklib`
- **Vault analytics:** Tool to find orphan notes, tag usage stats, broken links
- **Batch processing:** Process a YouTube playlist into a multi-part vault
- **Publish to registry:** Package for other Obsidian users to install

---

## Screenshot / visual capture — `capture_pdf_page` + `get_youtube_frames` (Phase 5)

> **Status:** SHIPPED. Implemented in [`tools/screenshots.py`](tools/screenshots.py),
> registered via `register_media_tools` in [`server.py`](server.py). Built for visual
> learners (medical students, artists): turn *a part of a source* into an HD image
> that lands in the vault and is ready to embed in the matching note.
>
> Two sources are supported. **`capture_pdf_page`** renders PDF pages (or a cropped
> region of a page) to a high-DPI PNG — pure Python via PyMuPDF, no system binaries,
> so it works out of the box. **`get_youtube_frames`** samples video frames via
> `yt-dlp` + `ffmpeg`; ffmpeg is resolved by `config.find_ffmpeg()`, which prefers a
> system `ffmpeg` on PATH and otherwise falls back to the static binary bundled with
> the `imageio-ffmpeg` pip package — so no separate ffmpeg install is required.
>
> Design decisions baked in: images save to **one central folder**
> (`config.get_screenshots_folder`, default `2 - Source Material/Screenshots`,
> override via `SCREENSHOTS_FOLDER`); the **full-resolution** image is saved while a
> **downscaled** copy is what the model sees (accurate captions, lower token cost);
> every saved image returns an Obsidian `embed` snippet naming its **source location**
> (page number / timestamp) and sized to `embed_width` (default 480px) so embeds show
> as thumbnails, not full-screen. Saves are guarded by `resolve_image_target` (no
> traversal, vault-only, never an ignored folder).
>
> Captions name the **source location**. For PDFs that mismatch the printed page
> number against the raw PDF index (front matter, roman-numeral preface, …),
> `capture_pdf_page` uses the PDF's own embedded page labels automatically
> (`page.get_label()`), so a caption reads `p.35 (PDF p.47)` rather than `p.47`.
> Label-less PDFs (many ebook exports) fall back to the raw index, with an optional
> `page_label_offset` to align them by hand.
>
> A third tool, **`crop_screenshot`**, crops an already-saved image to a kept region
> (Pillow) — the "fix it after the fact" path when a capture grabbed too much, with no
> need to re-render the PDF or re-download the video. Non-destructive by default
> (writes `"<name> cropped.png"`); `replace=true` overwrites the original.
>
> The draft below is the original design note kept for context.

### Why

Claude has no native video input — it cannot stream an `.mp4` the way Gemini can.
What it *can* do well is reason over **images** (many at once). So "let Claude watch
the video" really means: **sample frames from the video and hand them to Claude as
images, alongside the transcript we already extract.**

The transcript already captures everything *spoken*. Frames add the information the
words miss — **slides, charts, on-screen code, diagrams, demonstrations.** For this
vault's trading/finance content, that's the high-value case: capturing the actual
chart or framework on screen so generated notes (and their SVGs) reflect what was
shown, not just what was said. This pairs directly with the Phase 3 `process-youtube`
prompt (see "Integration" below).

### Tool definition: `get_youtube_frames`

**Purpose:** Extract representative frames from a YouTube video so Claude can analyse
the visuals, and optionally save them into the vault for embedding in notes.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `url` | string | yes | YouTube video URL (any format) |
| `mode` | string | no | `"scene"` (default — grab frames where the screen changes) or `"interval"` (every N seconds) |
| `interval_seconds` | integer | no | For `mode="interval"`: seconds between frames (default `30`) |
| `scene_threshold` | number | no | For `mode="scene"`: ffmpeg scene-change sensitivity 0–1 (default `0.4`) |
| `max_frames` | integer | no | Hard cap on frames returned (default `24`) — protects token budget |
| `start` / `end` | string | no | Optional time range (`"mm:ss"`) to limit extraction to a clip |
| `width` | integer | no | Downscale frames to this width in px (default `768`) to cut tokens |
| `return_images` | boolean | no | Return frames inline as MCP image content for analysis (default `true`) |
| `montage` | boolean | no | Return one tiled contact-sheet image instead of many separate blocks (default `false`) |
| `save_to` | string | no | Vault-relative folder to also write frames as PNGs (e.g. `2 - Source Material/<topic> frames`). Lets notes embed them via `![[frame.png]]`. |
| `ocr` | boolean | no | Also OCR each frame and return on-screen text (default `false`) |

**Returns:**

```json
{
  "video_id": "dQw4w9WgXcQ",
  "mode": "scene",
  "frame_count": 12,
  "frames": [
    {
      "index": 0,
      "timestamp_seconds": 14.0,
      "timestamp": "00:14",
      "saved_path": "2 - Source Material/Gold Trading frames/frame-0014.png",
      "ocr_text": "Support & Resistance — daily chart"
    }
  ],
  "montage_path": null
}
```

(When `return_images` is true, the frames are *also* attached as MCP image content
blocks so the model can see them. `saved_path`/`ocr_text` appear only when `save_to`
/ `ocr` are used.)

**Implementation notes:**
- **Download:** `yt-dlp` to fetch the stream (prefer a modest resolution, e.g. ≤720p,
  to keep download + frame size small). Download to a temp dir.
- **Extract:**
  - `mode="scene"` → `ffmpeg -i in.mp4 -vf "select='gt(scene,{threshold})',scale={width}:-1" -vsync vfr out-%04d.png` (grabs frames where the picture changes — new slide/chart).
  - `mode="interval"` → `ffmpeg -i in.mp4 -vf "fps=1/{interval},scale={width}:-1" ...`.
  - Always enforce `max_frames` (truncate, evenly sampled if over).
- **Return as images:** use FastMCP's `Image` type (`from mcp.server.fastmcp import Image`) so frames become image content blocks the model can view. NOTE: the Claude Code *terminal* can't render returned images, but the model still receives them; Claude Desktop renders them.
- **Montage (cost control):** when `montage=true`, tile keyframes into a single grid
  PNG (`ffmpeg ... tile=4x3`) and return that one image instead of many — far cheaper
  on tokens for a quick overview.
- **OCR (optional):** `pytesseract` + `Pillow` over each frame for slide/code/text-heavy
  videos; cheaper than images when you only need on-screen text. Requires the Tesseract
  binary.
- **Save to vault:** if `save_to` is set, write PNGs through the same
  `resolve_in_vault()` guard as every other write (no traversal, must land inside the
  vault). Never write into ignored folders (`7 - File Vault`, `8 - Quests`).
- **Cleanup:** delete the temp download + temp frames after returning (keep only what
  `save_to` persisted).

### New dependencies (heavier than v1)

This is the main trade-off — it breaks v1's "pure-Python, keyless, no-binaries" profile:

| Dependency | Kind | For |
|------------|------|-----|
| `yt-dlp` | pip | Downloading the video stream |
| **ffmpeg** | **system binary** (not pip) | Frame extraction, scene detection, montage |
| `pytesseract` + `Pillow` | pip (optional) | OCR mode |
| **Tesseract** | **system binary** (optional) | OCR mode |

Config could add an optional `FRAMES_TMP_DIR` and a default `save_to` base; `config.py`
should detect missing `ffmpeg`/`tesseract` and return a clear `{"error": ...}` instead
of crashing.

### Integration with the Phase 3 `process-youtube` prompt

Once this exists, the prompt's **Step 1 (Extract)** can optionally call
`get_youtube_frames` for visual-heavy videos and:
- use the frames/OCR to make module rewrites accurate to what was on screen,
- ground the generated **SVG diagrams** in the real charts/frameworks shown,
- and, when `save_to` is used, embed actual screenshots into the relevant
  `2 - Source Material` note via `![[frame.png]]`.

This stays optional so text-only talks don't pay the download/token cost.

### Trade-offs / constraints

- **Cost + latency:** download time + image tokens (~1–1.6k tokens/frame). Mitigate
  with `mode="scene"`, `max_frames`, `width` downscale, and/or `montage`.
- **System deps:** ffmpeg (and Tesseract for OCR) must be installed on the host.
- **YouTube ToS:** downloading video is a different posture than the read-only
  transcript/oEmbed calls — fine for personal/local knowledge use; worth noting.
- **Terminal rendering:** images are visible to the model, not in the Claude Code
  terminal itself.

### Testing checklist (when built)

1. `get_youtube_frames` (scene mode) on a slide/chart-heavy video returns a sensible
   set of frames under `max_frames`.
2. `mode="interval"` returns frames at the expected cadence.
3. `montage=true` returns a single tiled image.
4. `ocr=true` returns readable on-screen text.
5. `save_to` writes PNGs inside the vault (and refuses paths that escape it or target
   ignored folders).
6. Missing `ffmpeg` → clean `{"error": ...}`, not a crash.
7. Temp files are cleaned up; transcript/metadata/vault tools still work.

### Security note (addendum)

`get_youtube_frames` is the first tool that writes **non-`.md`** files (PNGs) and pulls
remote media. Saved frames still go through `resolve_in_vault()` (no traversal, vault-
only, never ignored folders). Downloads land in a temp dir and are cleaned up.
