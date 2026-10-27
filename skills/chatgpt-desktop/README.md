# ChatGPT Desktop

ChatGPT has no skill format like the others, so this folder holds plain **instructions**
(`instructions.md`) to paste into the instructions of a Project or a custom GPT.

## What is and isn't possible (please read)

From OpenAI's help article "Connecting custom MCP servers in ChatGPT":
- Custom MCP servers are added under **Plugins > Add custom MCP server** with a public HTTPS endpoint (or a Secure
  MCP Tunnel for private servers). Full MCP support is for Business and Enterprise/Edu plans; Pro is limited to
  read/fetch permissions. The article describes the web experience and does not mention the desktop app.
- The authentication it documents is **OAuth**. This server authenticates with a static bearer token and has no
  OAuth flow, and I could not confirm that ChatGPT can send a fixed `Authorization` header.

So a direct connection from ChatGPT to this server is **not confirmed to work**. Options if you need ChatGPT:
put an OAuth-capable gateway in front of the server (a bigger change, and one that affects the security model),
or use one of the other clients here. If you do get the server connected, the instructions below apply as is.

## Use

1. Connect the server as above.
2. Create a Project (or custom GPT) and paste the contents of `instructions.md` into its instructions. Length
   limits vary by feature; if the text is too long, keep the "Rules for write tools" and "Workflows" sections.
3. Keep ChatGPT's tool confirmation on for write tools.
