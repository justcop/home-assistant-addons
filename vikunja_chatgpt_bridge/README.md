# Vikunja Native MCP OAuth Bridge (experimental)

**This forwards Vikunja's native MCP tools.** It is an OAuth 2.1 authorization wrapper, not a separate MCP implementation using Vikunja REST.

`ChatGPT → OAuth bridge → Vikunja /api/v2/mcp`

## Installation

1. Update the add-on from this repository. **Version 0.2.0 changes the configuration field names**.
2. In Vikunja 2.7+, open **Settings → MCP**, generate an **MCP token** using the Typed tools read + write preset (exclude deletion initially). This is not a general REST API token.
3. In Home Assistant add-on Configuration:
   - `vikunja_mcp_url`: `https://vikunja.yourdomain.com/api/v2/mcp`
   - `mcp_base_url`: `https://vikunja-mcp.yourdomain.com`
   - `vikunja_mcp_token`: the token from Vikunja **Settings → MCP**
   - `oauth_password`: a unique password of at least 16 characters
   - `session_secret`: a stable random string of at least 32 characters, e.g. `openssl rand -hex 32`
4. Start the add-on.
5. In the **existing Cloudflare Tunnel**, add another public hostname `vikunja-mcp.yourdomain.com` pointing at `http://HOME_ASSISTANT_LAN_IP:3000`. Do not expose router port 3000 or place a Cloudflare Access login interstitial in front of this OAuth service.
6. Confirm `https://vikunja-mcp.yourdomain.com/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` return JSON.
7. In ChatGPT Plugins, add a custom MCP server pointing to `https://vikunja-mcp.yourdomain.com/mcp` and authenticate using OAuth. The authorization page asks for `oauth_password`.

## Implementation and security

The proxy performs dynamic client registration, PKCE-S256 authorization code exchange and rotating refresh-token issuance, then forwards authenticated MCP HTTP requests to Vikunja with the native MCP token. OAuth metadata is served at the standard well-known endpoints. The bridge stores OAuth state in its private `/data/oauth-state.json` and survives restarts.

It is a **single-owner experimental service**, not a multi-user identity provider. Anyone with the bridge password can obtain the full rights of its configured Vikunja MCP token. Keep the token and secrets inside Home Assistant options, never GitHub. A dedicated Vikunja bot identity with limited project access is recommended. Revoking the MCP token in Vikunja immediately revokes underlying task access.

**Not yet verified end-to-end with ChatGPT or a live Home Assistant deployment.** Test reads before enabling writes; avoid patient-identifiable data. Do not use for production-sensitive information until tested and security reviewed.
