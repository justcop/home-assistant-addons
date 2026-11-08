# Vikunja Native MCP OAuth Bridge (experimental)

**This forwards Vikunja's native MCP tools.** It is an OAuth 2.1 authorization wrapper, not a separate MCP implementation using Vikunja REST.

`ChatGPT → OAuth bridge → Vikunja /api/v2/mcp`

## Installation

1. Update the add-on from this repository. **Version 0.2.1 uses the configuration field names**.
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

## Version 0.2.1: OAuth registration compatibility

The bridge supports dynamic client registration for both public PKCE clients (`none`) and confidential clients (`client_secret_post` and `client_secret_basic`). The registration endpoint is advertised in `/.well-known/oauth-authorization-server` and a registered client secret is returned only for confidential clients. Restart the add-on after updating. If ChatGPT still reports registration failure, check Cloudflare Tunnel's HTTP POST forwarding and inspect the add-on logs for rejected requests; the issue may be network routing or registration metadata rather than these authentication methods.

Local diagnostic (replace hostname, do **not** include any secrets):

```bash
curl -i https://mcp.example.com/.well-known/oauth-authorization-server
curl -i -X POST https://mcp.example.com/register \\
  -H 'Content-Type: application/json' \\
  -d '{"redirect_uris":["https://chatgpt.com/connector_platform_oauth_redirect"],"token_endpoint_auth_method":"client_secret_post"}'
```

The POST should return HTTP 201 and a JSON response containing a `client_id` and `client_secret`. The test creates a client, so only run it once. Do not paste client secrets into chats.

## OAuth registration diagnostics (v0.2.2)

The bridge logs request **methods, paths, response status codes**, and DCR client authentication method, but does **not** log passwords, API tokens, authorization codes, or registered client secrets. This update also serves protected-resource metadata at both `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/mcp`.

1. Update to **0.2.2** and start the add-on.
2. Open `https://YOUR-MCP-HOST/.well-known/oauth-authorization-server` in a browser and confirm JSON including `registration_endpoint`.
3. Attempt to add the connector in ChatGPT again.
4. Copy only the `[OAuth]` log lines (not credentials). A `POST /register HTTP 400` indicates validation rejection. No `POST /register` indicates discovery or Cloudflare routing failure. A `POST /register HTTP 201` means registration succeeded and the error originates after successful registration.

A public MCP HTTPS hostname must forward to `http://HOME_ASSISTANT_LAN_IP:3000` via Cloudflare Tunnel, without a Cloudflare Access challenge or a redirect to Vikunja's own UI. The add-on cannot be validated against your Cloudflare route without knowing that hostname.
