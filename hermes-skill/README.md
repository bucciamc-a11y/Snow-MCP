# Hermes skill: servicenow

Kept separate from the server code. Format: `SKILL.md` with YAML frontmatter (`name`, `description`,
`version`, `required_environment_variables`, `metadata.hermes.{tags,category}`), per the Hermes skills
documentation (agentskills.io-style). I could not inspect your installed Hermes; verify with
`hermes skills list` after installing.

## Install (on the machine running Hermes)

```bash
mkdir -p ~/.hermes/skills/devops
cp -r servicenow ~/.hermes/skills/devops/
```
Alternatively add this directory's parent to `skills.external_dirs` in `~/.hermes/config.yaml`, or
`hermes skills install <path-or-url-to-SKILL.md> --name servicenow` if your version supports it.

Then complete the connection setup in `SKILL.md` ("Connection setup"): token in `~/.hermes/.env`,
`mcp_servers.servicenow` block in `config.yaml`, `hermes mcp test servicenow`.

No credentials are stored in the skill. If your Hermes version lacks `required_environment_variables`,
the field is ignored harmlessly — the token still comes from `~/.hermes/.env`.
