<div align="center">

<img src="assets/hero.svg" alt="Obsidian Knowledge Pipeline — an MCP server that turns YouTube videos into structured Obsidian notes" width="100%">

<br>

![Python](https://img.shields.io/badge/Python-3.13+-1f6feb?style=flat-square&logo=python&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-FastMCP-3c3489?style=flat-square)
![Transport](https://img.shields.io/badge/transport-stdio-085041?style=flat-square)
![API keys](https://img.shields.io/badge/API_keys-none-27500a?style=flat-square)
![Obsidian](https://img.shields.io/badge/Obsidian-Bases-6e56cf?style=flat-square&logo=obsidian&logoColor=white)

**A [Model Context Protocol](https://modelcontextprotocol.io) server that gives Claude safe, structured read/write access to an [Obsidian](https://obsidian.md) vault — and turns YouTube videos into beautifully structured, cross-linked notes.**

[Install](#-install) · [Features](#-features) · [Tools](#-tools) · [The pipeline](#-the-process-youtube-pipeline) · [Quick start](#-quick-start) · [Architecture](#-architecture) · [Roadmap](#-roadmap)

</div>

---

## 📦 Install

An MCP server isn't a global install — **each user registers it once in their own MCP client.** It takes about two minutes:

```bash
git clone https://github.com/zenobioscastillo1-source/obsidian-knowledge-pipeline
cd obsidian-knowledge-pipeline
uv sync                          # install dependencies
# then set VAULT_PATH in .env to your Obsidian vault's root folder
claude mcp add obsidian-knowledge-pipeline -- uv --directory "$PWD" run python server.py
```

Restart Claude Code and the 4 vault tools, 2 YouTube tools, and the `/process-youtube` prompt are available. Using Claude Desktop, the MCP Inspector, or plain pip instead? See [Quick start](#-quick-start). *(`$PWD` works in bash/zsh; on Windows PowerShell use the folder's absolute path.)*

---

## ✨ Features

<table>
<tr>
<td width="50%" valign="top">

**🎬 YouTube extraction**
Pull a video's full transcript (with language fallback) and its title/channel/thumbnail — via the public oEmbed endpoint. **No API key.**

</td>
<td width="50%" valign="top">

**🗂️ Safe vault I/O**
Search, read, create, and list notes — every path funnelled through one guard so access never escapes your `VAULT_PATH`.

</td>
</tr>
<tr>
<td width="50%" valign="top">

**🧩 One-prompt pipeline**
The `process-youtube` prompt drives the whole flow: extract → analyze → write a full, cross-linked note set into your vault.

</td>
<td width="50%" valign="top">

**🎨 Inline SVG diagrams**
Every module gets a hand-styled diagram inside an Obsidian callout — hub-and-spoke, stacks, journeys, comparisons.

</td>
</tr>
<tr>
<td width="50%" valign="top">

**🏷️ Tag deduplication**
Scans `3 - Tags` first and reuses existing concepts, only creating stubs for genuinely new ones — italic `*tags:*` wikilinks, not hashtags.

</td>
<td width="50%" valign="top">

**📊 Theme Bases**
The index is a native **Obsidian Base** per theme — grouped by source, auto-collecting every future note you process under that theme.

</td>
</tr>
</table>

---

## 🔧 Tools

### Vault

| Tool | What it does | Parameters |
| --- | --- | --- |
| `search_vault` | Find notes by filename, or by content | `query` *(required)*; `folder`, `search_content` *(default `false`)*, `max_results` *(default `10`)* |
| `read_note` | Read a note's full content + metadata | `path` *(required)* |
| `create_note` | Create a note, making parent folders as needed | `path`, `content` *(required)*; `overwrite` *(default `false`)* |
| `list_folder` | List files and subfolders of a vault directory | `path` *(default `""` = vault root)*; `recursive` *(default `false`)* |

All vault paths are **relative to the vault root** (e.g. `3 - Tags/Zettelkasten.md`). Anything that tries to escape the vault (absolute paths, `..`, symlinks pointing outside) is rejected.

### YouTube

| Tool | What it does | Parameters |
| --- | --- | --- |
| `get_youtube_transcript` | Extract a video's transcript (timed segments + concatenated text) | `url` *(required)*; `language` *(default `"en"`, falls back to the first available, preferring human-made captions)* |
| `get_youtube_metadata` | Fetch a video's title, channel name + URL, and thumbnail | `url` *(required)* |

`url` accepts any common form (`watch?v=`, `youtu.be/`, `embed/`, `shorts/`, with extra `&t=` / `&list=` params).

### Screenshots (visual capture)

For visual learners (medical students, artists, anyone who learns from diagrams): turn **a part of a source** into an HD image saved in the vault and ready to embed in the matching note. The full-resolution image is saved for the reader; a downscaled copy is what the model sees (accurate captions, lower token cost). Each saved image comes with an Obsidian `embed` snippet that names **where in the source** it came from (page number / timestamp).

| Tool | What it does | Parameters |
| --- | --- | --- |
| `capture_pdf_page` | Render PDF page(s) — or a cropped region of a page — to an HD PNG | `pdf_path` *(required; absolute path or vault-relative)*; `pages` *(default `"1"`, e.g. `"12-14"` / `"3,5,9"`)*, `source_name`, `region` *(`"x0,y0,x1,y1"` fractions)*, `dpi` *(default `300`)*, `page_label_offset` *(default `0`)*, `analysis_width` *(default `1024`)*, `embed_width` *(default `480`)*, `save` *(default `true`)*, `return_images` *(default `true`)* |
| `get_youtube_frames` | Sample frames from a video (scene-change or fixed interval) | `url` *(required)*; `mode` *(`"scene"` / `"interval"`)*, `interval_seconds` *(default `30`)*, `scene_threshold` *(default `0.4`)*, `max_frames` *(default `12`)*, `start`, `end`, `source_name`, `analysis_width`, `embed_width`, `save`, `return_images` |
| `crop_screenshot` | Crop an already-saved screenshot to just the part you want (fix a capture without re-rendering) | `image_path` *(required; the `saved_path` a capture returned)*, `region` *(required; `"x0,y0,x1,y1"` fractions to KEEP)*; `replace` *(default `false` → writes `"<name> cropped.png"`)*, `analysis_width`, `embed_width`, `return_image` |

Images save to one central folder (default `2 - Source Material/Screenshots`, override with the `SCREENSHOTS_FOLDER` env var). Each saved image returns an Obsidian `embed` snippet sized to `embed_width` (`![[img|480]]`) so it appears as a readable thumbnail rather than taking over the note — the saved file stays full resolution (click to enlarge). Captions use the **printed page number** when the PDF embeds page labels (so `p.35` matches the book, not the raw PDF index); for label-less PDFs, set `page_label_offset` to align them (e.g. `-12`). `capture_pdf_page` and `crop_screenshot` are pure Python (PyMuPDF / Pillow) and need no system binaries. `get_youtube_frames` uses `yt-dlp` + `ffmpeg`; both install with the package — `imageio-ffmpeg` ships a static ffmpeg binary, so **no separate ffmpeg install is required** (a system `ffmpeg` on `PATH` is used in preference if you have one).

---

## 🚀 The `process-youtube` pipeline

One MCP **prompt** orchestrates the tools into a full video → vault workflow.

<div align="center">
<img src="assets/pipeline.svg" alt="Pipeline: extract, scan vault, analyze, write to the vault" width="100%">
</div>

It splits the transcript into 3–7 cross-linked **modules** (each with an SVG diagram, beginner-friendly rewrites, tables, and italic `*tags:*` wikilinks), writes a per-source **overview note**, dedupes and creates **tag stubs**, and maintains a per-theme **Obsidian Base** index.

| Argument | Required | Description |
| --- | --- | --- |
| `url` | ✅ | YouTube video URL |
| `theme` | — | Theme that groups this note's Base (e.g. `AI`, `Trading`). **Inferred from the video if omitted.** |
| `topic_name` | — | Source/course title (used as the `source` property + overview note name). Derived from the video title if omitted. |
| `target_folder` | — | Subfolder within `6 - Main Notes` (defaults to the source title). |

> **Where it lands:** module notes + a `<Source> — Overview` note in `6 - Main Notes/`, new concept stubs in `3 - Tags/`, and a **`<Theme>.base`** in `4 - Indexes/`. Each note carries YAML frontmatter (`theme`, `source`, `type`, `module`, `summary`); the theme Base filters on `theme`, groups by `source`, and shows each module's `summary`. Because a Base is a live query, it's created **once** and every future video under that theme appears in it automatically.

In Claude Code or Claude Desktop, invoke it as a prompt/slash command (e.g. `/process-youtube`) and supply the URL.

---

## 🏁 Quick start

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
```

> Prefer plain pip? `python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt`

**Point it at your vault** — open `.env` and set the absolute path to your Obsidian vault root:

```
VAULT_PATH=D:\Obsidian\My Vault
```

No quotes needed even with spaces; `.env` is git-ignored. On macOS/Linux use a forward-slash path.

<details>
<summary><b>Optional: ignore personal folders</b></summary>

<br>

Top-level folders that aren't part of the pipeline can be hidden from `list_folder`, `search_vault`, and processing. Set `IGNORED_FOLDERS` in `.env` (comma-separated); it defaults to `7 - File Vault, 8 - Quests`. Ignored folders are skipped in sweeps but still reachable if you target one directly.

</details>

### Try it in the MCP Inspector

```powershell
uv run mcp dev server.py
```

Opens a browser UI (needs **Node.js / npx** on your PATH). Try `list_folder` with no arguments, or open the **Prompts** tab to run `process-youtube`.

### Connect to Claude Code

```powershell
claude mcp add obsidian-knowledge-pipeline -- uv --directory "ABSOLUTE\PATH\TO\obsidian-knowledge-pipeline" run python server.py
```

<details>
<summary><b>Connect to Claude Desktop</b></summary>

<br>

Add to `claude_desktop_config.json` (**Windows:** `%APPDATA%\Claude\…`, **macOS:** `~/Library/Application Support/Claude/…`):

```json
{
  "mcpServers": {
    "obsidian-knowledge-pipeline": {
      "command": "uv",
      "args": ["--directory", "ABSOLUTE/PATH/TO/obsidian-knowledge-pipeline", "run", "python", "server.py"]
    }
  }
}
```

If `uv` isn't on Claude Desktop's PATH, point `command` straight at `.venv/Scripts/python.exe` (Windows) or `.venv/bin/python` (macOS/Linux) with `server.py` as the only arg. Restart Claude Desktop after saving.

</details>

---

## 🏗️ Architecture

<div align="center">
<img src="assets/architecture.svg" alt="An MCP client talks over stdio to the FastMCP server, whose tools reach YouTube and the Obsidian vault through a path guard" width="100%">
</div>

Every tool resolves its `path` through `config.resolve_in_vault()` — the single security choke point that rejects absolute paths, resolves `..`/symlinks, and confirms the result is still inside `VAULT_PATH`. Tools return plain JSON and report problems as `{"error": "…"}` instead of crashing, so the client always gets a useful answer.

```
obsidian-knowledge-pipeline/
├── server.py                  # FastMCP entry point — registers tools + prompt, runs over stdio
├── config.py                  # VAULT_PATH + resolve_in_vault() path guard + ignore-list + screenshots folder
├── tools/
│   ├── vault.py               # search_vault · read_note · create_note · list_folder
│   ├── youtube.py             # get_youtube_transcript · get_youtube_metadata
│   └── screenshots.py         # capture_pdf_page · get_youtube_frames · crop_screenshot (HD images → vault)
├── prompts/
│   └── process_youtube.py     # the process-youtube prompt template
├── assets/                    # README diagrams (SVG)
├── .env                       # VAULT_PATH=…  (git-ignored)
├── pyproject.toml             # deps (uv)   ·   requirements.txt (pip / Inspector)
└── obsidian-mcp-spec.md       # full design spec
```

---

## 🗺️ Roadmap

- ✅ **Phase 1** — vault read/write tools
- ✅ **Phase 2** — YouTube transcript + metadata extraction
- ✅ **Phase 3** — the `process-youtube` prompt → structured notes + per-theme Obsidian Base index
- ✅ **Phase 5** — screenshot / visual capture: `capture_pdf_page` (PDFs, pure Python) + `get_youtube_frames` (video; ffmpeg bundled via imageio-ffmpeg), saving HD images to the vault for visual learners (see [the spec](obsidian-mcp-spec.md))
- 📇 **Registry-ready** — a [`server.json`](server.json) scaffold for the [official MCP registry](https://github.com/modelcontextprotocol/registry) is included; listing there also needs a PyPI release + namespace auth via the `mcp-publisher` CLI

---

## 📄 License

[MIT](LICENSE) © Zenobios Castillo

<div align="center">
<sub>Built with the official <a href="https://github.com/modelcontextprotocol/python-sdk">MCP Python SDK</a> · FastMCP · stdio</sub>
</div>
