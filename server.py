"""obsidian-knowledge-pipeline — MCP server entry point.

Exposes vault tools (search_vault, read_note, create_note, list_folder) and
YouTube extraction tools (get_youtube_transcript, get_youtube_metadata) over the
standard local stdio transport, so an MCP client (Claude Desktop, Claude Code,
the MCP Inspector) can browse/edit an Obsidian vault and pull source material
from YouTube.

Phase 1: vault tools. Phase 2: YouTube tools. Phase 3: the `process-youtube`
prompt that ties them together.
"""

from mcp.server.fastmcp import FastMCP

from prompts.process_youtube import register_youtube_prompt
from tools.vault import register_vault_tools
from tools.youtube import register_youtube_tools

mcp = FastMCP("obsidian-knowledge-pipeline")
register_vault_tools(mcp)
register_youtube_tools(mcp)
register_youtube_prompt(mcp)


if __name__ == "__main__":
    # stdio is the standard transport for local MCP servers.
    mcp.run(transport="stdio")
