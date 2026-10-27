## Connection setup (one time)

1. `~/.hermes/.env`: `SERVICENOW_MCP_TOKEN=<token from the server setup script>` (never in config.yaml).
2. `~/.hermes/config.yaml` (the server's setup script prints this block with the right `include:` list for
   the features you enabled):
   ```yaml
   mcp_servers:
     servicenow:
       url: "https://mcp.example.com/mcp"
       headers:
         Authorization: "Bearer ${SERVICENOW_MCP_TOKEN}"
       timeout: 60
       connect_timeout: 30
       tools:
         include: [search_incidents, get_incident, get_change_request]   # add enabled write tools here
   ```
3. `hermes mcp test servicenow`, then restart Hermes. Keep Hermes' own tool-approval prompts enabled for the
   write tools: a human should approve every write call.

If `${VAR}` expansion is unsupported in headers, use `hermes mcp add servicenow --url https://mcp.example.com/mcp`
and enter the Authorization header interactively. The server speaks Streamable HTTP at `/mcp` only.
