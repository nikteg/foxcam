# Accounts, sign-in and sharing

Everything except the worker API needs a signed-in user: the web UI, the JSON API, the
recordings, clips and snapshots, and the live view. Workers keep their own access tokens
([WORKER_PROTOCOL.md](WORKER_PROTOCOL.md)).

## First start

With no accounts yet, the server prints a one-time **setup code** in its log (also written
to `data/setup-code`). The web UI asks for it and creates the first account, an admin. The
code keeps whoever finds a fresh installation first from claiming it.

Alternatives: `mise run user:add -- you@example.org --admin` on the server, or list your
e-mail in `auth.admin_emails` and sign in with an OAuth provider.

## Signing in

- **E-mail and password.** Passwords are hashed with scrypt; repeated failures from one
  address or for one account are blocked for 15 minutes.
- **OAuth / OpenID Connect.** Google, Microsoft, GitHub, or any OIDC provider (Authentik,
  Keycloak, Authelia, Auth0, …):

  ```yaml
  auth:
    oauth:
      - id: google
        type: google
        client_id: ${GOOGLE_CLIENT_ID:-}
        client_secret: ${GOOGLE_CLIENT_SECRET:-}
      - id: authentik
        type: oidc
        name: Authentik
        issuer: https://auth.example.org/application/o/media-streamer/
        client_id: ${OIDC_CLIENT_ID:-}
        client_secret: ${OIDC_CLIENT_SECRET:-}
        allowed_domains: [example.org]   # optional
  ```

  Register `<how you open the UI>/api/auth/oauth/<id>/callback` as the redirect URL at the
  provider (e.g. `https://cams.example.org/api/auth/oauth/google/callback`; set
  `auth.base_url` if the server cannot tell from the request). Sign-ins use the
  authorization-code flow with PKCE, state and nonce.

  An external account is matched to an existing account only by an e-mail address the
  provider has **verified**. Microsoft does not say whether an address is verified unless the
  `xms_edov` optional claim is configured in the app registration; without it, connect
  Microsoft from the account page of an existing account instead.

- Accounts can use both: **Account → Sign-in with other accounts** connects or disconnects
  providers.

Sessions are a random token in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` over HTTPS),
valid for `auth.session_days` since the last visit. Only a hash is stored. Changing a password
signs out the account's other browsers; disabling an account signs it out everywhere. Writes
with a foreign `Origin` header are refused (CSRF).

## API keys

For scripts and other programs (Home Assistant, cron jobs, …): **Account → API keys → New
API key**, then send it with every request:

```sh
curl -H "Authorization: Bearer msk_…" https://cams.example.org/api/events
```

A key acts as the user who made it, with that user's rights at the time of each request
(a user's key sees only that user's cameras; the keys of a disabled or removed account stop
working), limited further by its **scopes**:

| Scope | Allows |
|---|---|
| `read` | GET requests: cameras, recordings, events, detections, live view over HLS. Always included. |
| `write` | Everything else the user may change: cameras, censor areas, feedback, sharing, live view over WebRTC. |
| `admin` | An admin's admin powers: users (including their passwords), workers and their tokens, profiles, notifications, settings, the job queue. Only admins can give it, and it is off unless ticked. |

An admin's key without `admin` still sees every camera, but counts as a regular user
everywhere else. Keys can also **expire**. Keys can't reach `/api/auth/`: no making more keys,
changing the password or signing in. Only a SHA-256 of each key is stored, and the key
itself is shown once; revoke it on the Account page. Workers use their own tokens
(`msw_…`, Jobs → Add worker), not API keys.

## Who may create an account

`auth.registration`:

| Value | Who can sign up |
|---|---|
| `invite` (default) | only with an invite link |
| `open` | anyone who reaches the server (they see nothing until something is shared) |
| `closed` | nobody; admins add accounts (Settings → Users, or `media-streamer user add`) |

E-mails in `auth.admin_emails` can always sign up through OAuth and are admins once the address is verified (see Signing up).

## Roles

**Admins** see and change everything: all cameras, workers, detection profiles,
notifications, storage, the Tapo account, users and the configuration.

Everyone else only sees the cameras shared with them, and what those cameras recorded
(recordings, events, detections, snapshots, live view). Per camera, a user is:

| Role | Can |
|---|---|
| `viewer` | live view, recordings, events, detections, downloads |
| `editor` | + censor areas, private flag, detection profiles, recording profile, name, segment length, enable/disable, ✅/❌ feedback |
| `owner` | + share the camera and remove it |

Whoever adds a camera owns it (`auth.users_can_add_cameras: false` reserves that for
admins). Cameras from `config.yaml` have no owner: admins share them. Adding Tapo cloud
cameras is admin-only, since the Tapo account is the server's.

## Sharing

On the Cameras page, **Share** on a camera you own:

- **With an existing account**: enter its e-mail and pick a role.
- **With anyone else: an invite link.** It works once, for 7 days, and lets the recipient
  create an account (even with `registration: invite`) or accept with an existing one; it
  grants the chosen role on the camera. Anyone with the link can use it, so send it
  privately. Open links are listed in the dialog and can be revoked.

Admins also create account-only invite links (optionally making the new account an admin)
under Settings → Users. Users can stop seeing a shared camera from their account page.
Removing a camera removes its shares.

## Live view

Browsers never talk to MediaMTX. They load MediaMTX's WebRTC and HLS players through the
server, at `/api/live/<camera>/<main|low>/<webrtc|hls>/`, which checks the session and the
user's access to that camera before forwarding. Only the camera's own stream and its
low-data copy are reachable, never `<camera>/raw` (the picture before burned-in censor
areas).

MediaMTX's API, HLS and WebRTC servers listen on 127.0.0.1, and video (publish, read) and
the control API both need the `media-streamer` login: the server, ingest and the
transcoders MediaMTX starts sign in with a password kept in `data/mediamtx/password` (or
`mediamtx.password` in config.yaml). Being on the same machine is not enough: a camera's
source URL can point back at MediaMTX (`rtsp://127.0.0.1:8554/<other camera>/raw`), and
the API can add paths whose `runOnInit` runs a command. Only metrics stay open to local
processes. Start MediaMTX with `media-streamer mediamtx [mediamtx.yml] [--binary PATH]
[--password-file FILE]`, which hands it the password (`mise run mediamtx`, `scripts/dev.sh`
and docker-compose.yml do). In Docker, the server and ingest share MediaMTX's network
namespace for the API, and the MediaMTX container only gets `data/mediamtx/` (the
password) and `data/recordings/`. WebRTC media flows directly between the browser and
MediaMTX's port 8189, only for sessions set up through the server.

To watch over RTSP from another device (VLC, Home Assistant), add a user with a password
and `read` permission to `mediamtx.yml` (an example is in the file) and publish port 8554.

## Signing up

Self sign-up with a password (`registration: open`, or with an invite link) does not prove
the e-mail address. Such accounts get no rights from `admin_emails` and are not matched to
OAuth sign-ins by address; connecting an OAuth account with the same, confirmed, address
on the account page verifies it. Accounts made by an admin, the setup page, `media-streamer
user add` or OAuth count as verified. `admin_emails` therefore never skip the invite for
password sign-up: those admins sign in with OAuth, or are added on the command line.

## Exposing it to the internet

Put a TLS-terminating reverse proxy in front of port 8000 only, e.g. Caddy:

```
cams.example.org {
  reverse_proxy localhost:8000
}
```

Set `public_url: https://cams.example.org` (OAuth redirect URLs, links, and the `Secure`
flag on the session cookie) and open TCP 8189 for WebRTC media. Never proxy MediaMTX's own
ports.

The server believes `X-Forwarded-For` / `-Proto` only from `trusted_proxies` (config.yaml,
or `$FORWARDED_ALLOW_IPS`; default 127.0.0.1, a proxy on the same machine). In Docker the
proxy connects from another address, e.g. the bridge gateway `172.18.0.1` (`docker network
inspect`): set it there, or every visitor looks like the proxy, and the login limits (10
failed attempts per address in 15 minutes) hit everyone at once. Never `*` while the server
can also be reached without the proxy: anyone could then claim any address.

## Login limits

Failed password sign-ins are limited per address (10 in 15 minutes), per address and
account (10), and per account from anywhere (100, against guessing from many addresses).
So that a stranger can't use the per-account limit to lock the owner out, every sign-in
(password, OAuth, setup, sign-up) also sets a **known-device cookie** (`ms_device`, one
year, HttpOnly, SameSite=Lax, Secure like the session cookie, sent only to `/api/auth/`).
It is signed with `data/secret.key` and names the account and its password generation,
and grants no access by itself: it only makes a browser that has signed in to the account
before skip the per-account limit, and its failed attempts don't count towards it. A cookie
for another account, a tampered one, or one from before the last password change is
ignored, so changing the password also retires the devices.

## Turning it off

`auth.enabled: false` removes all logins: everyone who reaches the server is an admin. Only
for a trusted network.

## Command line

```sh
media-streamer user add you@example.org --admin [--name "You"] [--no-password]
media-streamer user password you@example.org
media-streamer user list
media-streamer user admin someone@example.org [--off]
media-streamer user remove someone@example.org
media-streamer reset users          # all accounts, sessions, shares, invites
```
