# Setup: OpenClaw

## 1. Install the skill

Copy the `servicenow` folder into the workspace skills directory (OpenClaw also loads `~/.agents/skills` and
`skills.load.extraDirs`):

```bash
cp -r skills/openclaw/servicenow ~/.openclaw/workspace/skills/
```

## 2. Connect the MCP server

Servers are defined under `mcp.servers`. Documented form for a remote Streamable HTTP server:

```bash
openclaw mcp add servicenow \
  --url https://<your-mcp-host>/mcp \
  --transport streamable-http \
  --include 'search_incidents,get_incident,get_change_request'   # add the write tools you enabled
openclaw mcp doctor servicenow --probe
```

**Authentication (not verified):** the OpenClaw pages I could read describe `url`, `transport` and `toolFilter` but
not how to attach an `Authorization: Bearer <token>` header; they say sensitive headers should go through the
supported secret mechanism. Check `openclaw mcp add --help` and the MCP CLI reference for the header option, and
keep the token out of the skill and out of version control. Run `openclaw mcp doctor servicenow --probe` to
confirm the server answers (a 401 means the header is missing or wrong).

Use `--include` to expose only the tools the administrator enabled on the server.
