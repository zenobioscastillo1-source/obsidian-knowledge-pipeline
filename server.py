"""obsidian-knowledge-pipeline — MCP server entry point.

Exposes vault tools (search_vault, read_note, create_note, list_folder) and
YouTube extraction tools (get_youtube_transcript, get_youtube_metadata) over the
standard local stdio transport, so any MCP client (Claude Code, Claude Desktop,
Codex, Cursor, the MCP Inspector, …) can browse/edit an Obsidian vault and pull
source material from YouTube. Nothing here is provider-specific.

It also exposes screenshot tools (capture_pdf_page, get_youtube_frames) that
turn a part of a source into an HD image saved in the vault for visual learners.

Phase 1: vault tools. Phase 2: YouTube tools. Phase 3: the `process-youtube`
prompt that ties them together. Phase 5: screenshot/visual-capture tools.
"""

from mcp.server.fastmcp import FastMCP

from prompts.canvas import register_canvas_prompt
from prompts.process_youtube import register_youtube_prompt
from prompts.voice import register_voice_prompt
from tools.canvas import register_canvas_tools
from tools.screenshots import register_media_tools
from tools.vault import register_vault_tools
from tools.youtube import register_youtube_tools

mcp = FastMCP("obsidian-knowledge-pipeline")
register_vault_tools(mcp)
register_youtube_tools(mcp)
register_media_tools(mcp)
register_canvas_tools(mcp)
register_youtube_prompt(mcp)
register_voice_prompt(mcp)
register_canvas_prompt(mcp)


if __name__ == "__main__":
    # stdio is the standard transport for local MCP servers.
    mcp.run(transport="stdio")
