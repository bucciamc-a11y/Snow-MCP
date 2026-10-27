# Setup: any SKILL.md client (generic)

`servicenow/SKILL.md` uses only the two fields every `SKILL.md`-style client understands (`name` and
`description`) and contains no client-specific setup, so it can be imported anywhere that loads a folder with a
`SKILL.md`: Claude (Desktop, Code, claude.ai), OpenClaw, Hermes, Paperclip, Codex-style CLIs and others. Use the
client-specific folders in `skills/` (each has its own `SETUP.md`) when you want the connection steps and that client's extra metadata.

## Use

1. Connect the MCP server in your client first (Streamable HTTP, URL `https://<your-mcp-host>/mcp`, header
   `Authorization: Bearer <token>`). Each client does this differently; see its folder in `skills/`.
2. Copy or import the `servicenow` folder the way your client loads skills (a skills directory, a zip upload, or an
   import-from-path/URL option).
3. If your client has no skill loader at all, paste the body of `SKILL.md` (everything after the `---` block) into
   its system prompt, custom instructions or project instructions.

The tool names in the skill are the server's own names; a client may display them with a prefix, which the skill
accounts for. Keep the client's tool-approval prompts on for write tools: the server previews every write, and a
human should approve the real call.
