# Authentication (design, not implemented)

Status: **not built.** In development the web UI and API are open to anyone who can reach the
server. Do not expose port 8000 (or MediaMTX's 8554/8888/8889) to the internet until this is done.

## Goals

- Anyone in the family can look at the status, live view, fox events and recordings.
- Only admins can change things: censor areas, cameras, Tapo account, feedback, job retries,
  and later deleting recordings.
- Workers authenticate separately from people (already possible: `worker_token`).
- Live view (served by MediaMTX, not by the server) is protected with the same login.
- Works on phones/iPad, ideally without a separate password for each device.

## Roles

| Role | Can |
|---|---|
| `viewer` | GET everything in the UI/API: status, live, events, detections, recordings, downloads. |
| `admin` | Everything a viewer can, plus all writes: masks, cameras, integrations, feedback, requeue, settings. |
| `worker` | Only `/api/worker/*`. Uses a bearer token, not a user login. |

## Recommended design: reverse proxy + OIDC

```
internet ──► Caddy (TLS, :443) ──► oauth2-proxy / Authelia (OIDC login) ──► server :8000
                                                                       └──► MediaMTX :8889/:8888
```

- **Caddy** terminates TLS (automatic Let's Encrypt) and is the only exposed port.
- **oauth2-proxy** (or Authelia / Authentik) handles login with Google/Microsoft/GitHub or a
  local user list, and forwards the user's e-mail and groups in headers
  (`X-Forwarded-Email`, `X-Forwarded-Groups`).
- The server trusts these headers **only** from the proxy (bind the server to localhost or a
  private Docker network) and maps groups to roles:
  `admin_emails` / `viewer_emails` (or groups) in `config.yaml`.
- One FastAPI dependency checks the role per route: GET routes need `viewer`, all
  POST/PUT/PATCH/DELETE routes need `admin`. `/api/worker/*` keeps its bearer token and
  bypasses the proxy login (separate Caddy route).
- MediaMTX live view goes through the same proxy, so its WebRTC/HLS players need the same
  session cookie. Alternatively use MediaMTX's `authHTTPAddress` to ask the server
  "may this user read path X?".

Why this design: no password handling in our code, single sign-on across devices, and the
proxy can be swapped (Tailscale, Cloudflare Access) without code changes.

## Simpler alternatives

| Option | Good for | Trade-off |
|---|---|---|
| **Tailscale only** (no public port) | family use, zero code | Every device needs Tailscale; no viewer/admin split unless added later. |
| **Cloudflare Access** in front | public URL without opening ports | Cloudflare sees the traffic. Same header-based roles as above. |
| **Built-in login** (sessions + argon2 password hashes in SQLite) | no extra services | We own password reset, brute-force protection, CSRF. Most code and most risk. |

## Implementation checklist (when we build it)

1. `auth` section in `config.yaml`: mode (`none` / `proxy-headers`), trusted proxy IPs, admin and
   viewer e-mails or groups.
2. FastAPI dependency `require_role("viewer" | "admin")` on all routers; worker router unchanged.
3. `GET /api/me` → `{email, role}`; the UI hides admin actions for viewers.
4. Caddyfile + oauth2-proxy service in `docker-compose.yml` (profile `auth`).
5. MediaMTX: `authMethod: http` pointing at a new `/api/mediamtx-auth` endpoint, or route
   live view through the proxy.
6. Make `worker_token` mandatory when auth is on.
7. Tests: viewer gets 403 on writes, admin allowed, missing/forged headers rejected when the
   request does not come from the proxy.
