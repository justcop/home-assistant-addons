# Vikunja ChatGPT MCP Bridge (experimental)

Home Assistant add-on wrapper for the third-party [Jacob-Tate/vikunja-mcp](https://github.com/Jacob-Tate/vikunja-mcp) OAuth-enabled MCP implementation. This is **not** Vikunja's native MCP endpoint; it uses Vikunja's REST API via a dedicated bot API token.

> Experimental and not yet tested end-to-end with ChatGPT or Home Assistant. Review the upstream image before granting wide privileges. The Dockerfile uses a floating upstream image tag; pin an audited immutable digest for production.

## Installation from this repository

1. In Home Assistant, ensure the repository `https://github.com/justcop/home-assistant-addons` is added in Settings → Add-ons → Add-on Store → Repositories.
2. Refresh the add-on store. Install **Vikunja ChatGPT MCP Bridge (experimental)**.
3. In Vikunja, create a dedicated bot user, share only the projects it should manage, and issue an API token in **Settings → API Tokens**. This bridge does **not** use Vikunja's native MCP token.
4. In the add-on Configuration tab, configure:
   - `vikunja_url`: your existing public Vikunja URL, e.g. `https://tasks.example.com`.
   - `mcp_base_url`: a **different** public hostname, e.g. `https://vikunja-mcp.example.com`.
   - `vikunja_token`: your bot user API token (keep private).
   - `mcp_auth_password`: a separate long unique password for the OAuth login page.
   - `jwt_secret`: random string of at least 32 characters; keep stable between restarts (for example `openssl rand -hex 32`).
5. Start the add-on and inspect its logs. Only amd64 is currently declared.

## Cloudflare Tunnel

You already expose Vikunja through Cloudflare Tunnel. Add **another Public Hostname** to that tunnel, such as `vikunja-mcp.example.com` with service `http://<HOME_ASSISTANT_LAN_IP>:3000`. Use your Home Assistant host's LAN IP, **not** the Vikunja URL and not the Home Assistant ingress URL.

- Set `mcp_base_url` to the new hostname with `https://`, no trailing path.
- Cloudflare manages public TLS; the tunnel forwards to the local HTTP add-on port.
- Keep the add-on port local to your trusted network; do **not** forward port 3000 from your router.
- Do **not** put Cloudflare Access interstitial or email OTP in front of the MCP hostname, as ChatGPT cannot complete that additional challenge in normal MCP discovery/OAuth requests.
- Confirm `https://vikunja-mcp.example.com/.well-known/oauth-authorization-server` is reachable and shows authorization metadata. If not, check add-on and tunnel logs.
- In ChatGPT, add a custom MCP server pointing to `https://vikunja-mcp.example.com/mcp` and select OAuth.

## Security and limitations

- **Do not commit credentials to GitHub**. Enter them only in Home Assistant add-on Configuration.
- Initially use read-only token scopes to validate listing, then enable task/project/label/comment writes, excluding permanent deletion.
- Do not grant bot access to patient-identifiable clinical information.
- ChatGPT OAuth compatibility of the third-party server is **unverified**; dynamic client registration and OAuth metadata may require upstream changes.
- The add-on inherits a third-party image which has not been independently audited. It must not be treated as production-ready without testing.
- To revoke: revoke the Vikunja bot API token, rotate `jwt_secret`, and remove the custom ChatGPT connection.
