# Setup: Paperclip

Paperclip does not run MCP itself: tools come from the **runtime adapter** an agent uses (Claude Code or Hermes),
and the skill is a Paperclip company skill. So there are three steps.

## 1. Give the agent's runtime the MCP server

Store the token as a Paperclip secret and pass it into the agent's `env` as a `secret_ref` (never in a file).

*Claude Code adapter* (run once per host, user scope so the token is not committed to `.mcp.json`):

```bash
claude mcp add --transport http --scope user servicenow https://<your-mcp-host>/mcp \
  --header "Authorization: Bearer $SERVICENOW_MCP_TOKEN"
claude mcp list        # servicenow should show as connected
```

*Hermes adapter:* include `mcp` in the adapter's `toolsets` (for example `"terminal,file,mcp"`) and add to
`~/.hermes/config.yaml` (host-wide):

```yaml
mcp_servers:
  servicenow:
    transport: http
    url: https://<your-mcp-host>/mcp
    headers:
      Authorization: "Bearer ${SERVICENOW_MCP_TOKEN}"
```

## 2. Import the skill

Import `skills/paperclip/servicenow` (a folder with `SKILL.md`) into the company: from this repository on GitHub,
from an approved local path, or a raw URL to `SKILL.md`. Paperclip's frontmatter parser supports flat scalars
only, so the generated file sticks to `name`, `slug` and `description`.

## 3. Attach it to the agent

Pass `desiredSkills` when hiring the agent, or `POST /api/agents/{agentId}/skills/sync` with
`{"desiredSkills": ["servicenow"]}`. Adapters whose sync mode is `unsupported` record the assignment but cannot
push the files.

Keep the runtime's tool-approval prompts on for write tools. (Setup details are from Paperclip's public docs; the
exact canonical skill key depends on how you import it.)
