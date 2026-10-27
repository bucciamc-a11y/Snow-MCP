# Skills for AI clients

One skill, packaged for each client. All of them teach the agent the same things: which tools exist, the
preview-then-confirm rule for writes, the prompt-injection rule, the triage / change-approval / on-call / problem
workflows, and what to do on each error.

| Client | Folder | Format | Notes |
|---|---|---|---|
| Any client (generic) | [`generic/`](generic/servicenow/SETUP.md) | `SKILL.md`, no client-specific parts | Start here if yours isn't listed |
| Hermes | [`hermes/`](hermes/servicenow/SETUP.md) | `SKILL.md` | Includes the Hermes connection steps |
| Claude Desktop | [`claude-desktop/`](claude-desktop/servicenow/SETUP.md) | `SKILL.md` (upload as a zip) | Connector setup depends on your plan |
| OpenClaw | [`openclaw/`](openclaw/servicenow/SETUP.md) | `SKILL.md` | Header-auth syntax not verified |
| Paperclip | [`paperclip/`](paperclip/servicenow/SETUP.md) | `SKILL.md` | MCP is configured in the agent's runtime |
| ChatGPT Desktop | [`chatgpt-desktop/`](chatgpt-desktop/SETUP.md) | Plain instructions (no skill format) | Direct connection is limited, see its README |

The `SKILL.md` / `instructions.md` files are **generated**. To change the wording, edit `_source/body.md` (shared)
or the entry for one client in `build.py`, then run:

```bash
python3 skills/build.py          # rewrite the generated files
python3 skills/build.py --check  # fail if any are out of date (the test suite does this)
```

Each skill folder carries its own `SETUP.md` with the install and connection steps for that client, so the
instructions travel with the skill.

None of these hold secrets. The MCP token is entered in each client's own secret store or config, never in a skill.
Setup details were taken from each product's public documentation; where something could not be confirmed it is
marked "not verified" in that client's README.
