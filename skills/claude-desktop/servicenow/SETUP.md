# Setup: Claude Desktop

Two separate pieces: the **skill** (instructions) and the **connection** to the MCP server (the tools).

## 1. Install the skill

Zip the `servicenow` folder so the folder itself is at the top of the zip, then upload it in Claude's skills
settings (Settings > Customize > Skills > upload; menu names vary by version):

```bash
cd skills/claude-desktop && zip -r servicenow.zip servicenow   # run from the repository root
```

## 2. Connect the MCP server

This server uses a static **bearer token**, not OAuth. Pick the route that fits your plan and network.

**A. Custom connector with a request header (remote URL).** Customize > Connectors > Add custom connector:
URL `https://<your-mcp-host>/mcp`, Authentication **No sign-in**, then under **Request headers** add
`Authorization` with the value `Bearer <your MCP token>` (the word `Bearer`, a space, then the token). Request
headers are a beta feature that is only available to some organizations; if you don't see the section, use B.
Claude reaches remote connectors from Anthropic's side, so the server must be reachable over public HTTPS or via
Anthropic's MCP tunnels (see Anthropic's "Add a connector that isn't in the directory" page).

**B. Local bridge in `claude_desktop_config.json`** (works for a private server). Uses the community
`mcp-remote` package; this route was not tested here:

```json
{
  "mcpServers": {
    "servicenow": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://<your-mcp-host>/mcp",
               "--header", "Authorization:${AUTH_HEADER}"],
      "env": { "AUTH_HEADER": "Bearer <your MCP token>" }
    }
  }
}
```

Keep Claude's per-tool approval on for write tools and don't choose "Always allow" for them: the server previews
every write, and a human should approve the real call.
