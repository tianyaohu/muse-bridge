# Architecture

`muse-bridge` is a remote MCP server (Streamable HTTP) that Meta Muse calls as a custom connector. It stores records for a Grok Bot to pull. This document is the multi-user core: one process, many users, each user sees only their own inbox.

Frank's original single-user job-finds bridge is still supported. `report_job_finds`, `list_recent_finds`, `send_message_to_nori`, and `ping` keep their names and result shapes. New callers use `submit_item`, `list_items`, `send_message`, and `delete_my_data`. Nori-specific names that remain (`send_message_to_nori`, the default server name `nori-muse-bridge`) are compatibility defaults, not the data model.

## Entrypoints

| What | Path |
|---|---|
| App factory | `muse_bridge.app:create_app` |
| Module-level ASGI app | `muse_bridge.app:app` |
| Process | `python server.py` or `python -m muse_bridge` (`server.py` is a shim) |
| Admin | `python -m muse_bridge.admin` |

`create_app(settings=None, *, oauth_verifier=None)` returns a Starlette app. Each call builds its own MCP server, inbox, and key store. The module-level `app` is built from the environment at import, which is what `uvicorn muse_bridge.app:app` and `python server.py` serve.

`/healthz` and `/` are registered on that app. They do not require auth. For the default server name the body is exactly:

```json
{"ok": true, "server": "nori-muse-bridge", "mcp": "/mcp"}
```

Any HTTP method on those two paths returns that body. `MUSE_BRIDGE_SERVER_NAME` changes the `server` field.

### Limits hook

`create_app` ends in `_install_limits_middleware`. When `MUSE_BRIDGE_LIMITS_ENABLED` is true it imports `RateLimitMiddleware` and `PayloadLimitMiddleware` from `muse_bridge.limits` and adds them with `app.add_middleware`. The import is guarded: if that module is missing, the app logs a warning and still starts.

Both middleware classes are constructed as `(app)` only. Read settings from the environment or from `scope["app"].state.settings` inside `__call__`. `PayloadLimitMiddleware` is the outer of the two. `/healthz` is inside the app, so a rate limiter that should ignore probes needs to skip that path itself.

`app.state` also holds `settings`, `store`, `key_store`, `mcp`, and `oauth`.

## Components

```
Muse  --Bearer / X-API-Key-->  Starlette app
                                 |-- /healthz, /                         (no auth)
                                 |-- /.well-known/oauth-protected-resource
                                 |-- /api/inbox                          (caller only)
                                 +-- /mcp  Streamable HTTP
                                       |-- auth sets Principal + store
                                       +-- tools in muse_bridge/tools/
                                             |
                                             v
                                       InboxStore  (JSONL today)
                                       users/<user_id>/records.jsonl

Grok Bot --same credential--> GET /api/inbox
         or python sync_inbox.py --> local inbox/*.jsonl
```

| Piece | Role |
|---|---|
| `muse_bridge/app.py` | ASGI app, MCP wiring, `/healthz`, pull/delete API, lifespan, limits hook |
| `muse_bridge/config.py` | Environment settings |
| `muse_bridge/auth/` | API keys, legacy token, OAuth resource-server seam |
| `muse_bridge/storage/` | `InboxStore` protocol and the JSONL implementation |
| `muse_bridge/domain.py` | Record shapes, dedupe, listing, deletion |
| `muse_bridge/tools/` | One module per MCP tool, with read/write annotations |
| `muse_bridge/admin.py` | Create, list, and revoke keys; purge; delete a user; import old JSONL |
| `sync_inbox.py` | Pull `/api/inbox` into the box's `inbox/` directory |

Tool handlers read the caller from context variables set by the HTTP auth wrapper. `anyio` copies that context into the worker thread the SDK uses for sync tools.

## Auth flow

Every route except `/healthz`, `/`, and `/.well-known/oauth-protected-resource` (plus the `/mcp` path variant) requires a credential:

1. `Authorization: Bearer <token>` (preferred)
2. `X-API-Key: <token>`
3. `?key=` is **deprecated and ignored** unless `MUSE_BRIDGE_ALLOW_QUERY_KEY=1`. When that flag is on, a successful query credential adds a `Deprecation: true` response header. The query string is how tokens end up in access logs. Do not turn the flag on for a public host.

A presented token is resolved in this order:

1. If it matches `mb_<8 hex>_<secret>`, it is an API key and nothing else. A wrong secret or a revoked key is a 401. It does not fall through to the legacy token or OAuth.
2. If `MUSE_BRIDGE_LEGACY_SINGLE_TOKEN=1`, the token is compared with `MUSE_BRIDGE_TOKEN` in constant time (both sides are SHA-256 digested, then `hmac.compare_digest`). A match is the single user `MUSE_BRIDGE_LEGACY_USER` (default `legacy`).
3. Otherwise `OAuthBearerValidator` runs, if a verifier was passed to `create_app`.

The server compares secrets with `hmac.compare_digest` on fixed-length digests, including a dummy compare when the prefix is unknown. `python server.py` refuses to start when legacy mode is on and `MUSE_BRIDGE_TOKEN` is empty. The same check runs in the ASGI lifespan, so `uvicorn muse_bridge.app:app` fails startup in that misconfiguration too.

API keys and the legacy token carry both `inbox:read` and `inbox:write`. `GET /api/inbox` and the list tools require read. Writes, `DELETE /api/inbox`, and `delete_my_data` require write. A verifier may return a narrower set; missing scope is HTTP 403 on the pull API and a tool error on MCP. `ping` only requires a valid credential.

401 bodies stay `{"error": "unauthorized"}`. The `WWW-Authenticate` header is:

```
Bearer realm="muse-bridge", resource_metadata="<origin>/.well-known/oauth-protected-resource", error="invalid_token"
```

### API keys

```
python -m muse_bridge.admin create-key --user alice
python -m muse_bridge.admin list-keys [--user alice]
python -m muse_bridge.admin revoke-key --prefix <8 hex>
python -m muse_bridge.admin revoke-key --key-id key_<hex>
```

`create-key` prints the token once. The file `data/keys.json` (mode `0600`) stores `prefix`, `secret_hash` (SHA-256 of the whole token), `hash_alg`, `user_id`, `created_at`, `last_used_at`, `revoked_at`, and `label`. The prefix is an index, not a secret. Revoked rows stay so a prefix is not issued again. `last_used_at` is updated at most once a minute. The admin CLI and the server lock the file with `flock`, so a revoke is visible to the server without a restart.

User ids are one path segment: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. They are case-sensitive.

### Legacy single-user mode

Set `MUSE_BRIDGE_LEGACY_SINGLE_TOKEN=1` and `MUSE_BRIDGE_TOKEN` to keep Frank's current Muse connector and `sync_inbox.py` / `test_client.py` working. That token is one user. It is not a multi-tenant credential. A `MUSE_BRIDGE_TOKEN` without the flag is ignored, and the server logs that fact. Existing installs that only set the token need the flag or every request is 401.

### OAuth 2.1 seam

This build is a **resource server**, not an authorization server. There is no `/authorize`, token endpoint, or dynamic client registration.

What is implemented:

* `GET /.well-known/oauth-protected-resource` and `GET /.well-known/oauth-protected-resource/mcp` return RFC 9728 protected-resource metadata (no auth, CORS `*`). `resource` is `<public origin>/mcp`. `bearer_methods_supported` is `["header"]`. `scopes_supported` is `inbox:read` and `inbox:write`.
* `OAuthBearerValidator` in `muse_bridge.auth.oauth` is the bearer-validation interface. Pass an async verifier to `create_app(..., oauth_verifier=...)`. The verifier receives `(token, resource, issuer)` and returns a `Principal` or `None`. With no verifier, OAuth tokens fail closed and API keys still work.
* When `MUSE_BRIDGE_OAUTH_ISSUER` is set, `authorization_servers` lists that issuer. When it is unset, `authorization_servers` is `[]` and `muse_bridge.oauth` is `not_configured`. An empty list is not a finished OAuth deployment; it tells clients the issuer is not advertised yet.

A follow-up should add a real verifier (JWT + JWKS, or RFC 7662 introspection) against that issuer and check the token audience against the `resource` value. Building a full authorization server is a separate project and is not required for per-install API keys.

## Data model

All of a user's records live in one JSONL file. `InboxStore.append` overwrites `user_id` with the authenticated user, so a tool cannot write another user's id into its own file.

Common fields: `id`, `kind`, `type` (same value as `kind`, kept for `sync_inbox.py`), `received_at` (UTC, `timespec="seconds"`), `source`, `status` (`new`), `user_id`.

| kind | id | shape |
|---|---|---|
| `job_find` | `find_<batch>_<index>` | Flat: `title`, `company`, `url`, `location`, `posted`, `notes`, `dedupe_key`, `batch`, `batch_note`. Same fields the original bridge stored, plus `kind` and `user_id`. |
| `message` | `msg_<10 hex>` | Flat: `subject`, `text`, `urgency`. |
| anything else matching `^[a-z][a-z0-9_]{0,31}$` | `itm_<12 hex>` | `payload` object. |

`kind=job_find` on `submit_item` uses the job schema and dedupe, not a free-form payload. `kind=message` stores the message fields.

Dedupe for jobs is per user: SHA-1 (16 hex chars) of the lowercased URL without a trailing slash, or `title|company` when the URL is empty. The same posting can exist once per user. A second `report_job_finds` of the same URL skips it. Generic kinds and messages dedupe only when the caller passes `dedupe_key` (stored as a SHA-256 prefix, not the raw key).

`GET /api/inbox?since=<iso>&type=all|finds|messages` returns `{"count", "records"}` for the caller, oldest first by absolute time. `since` is exclusive. Both `since` and `received_at` are parsed as ISO-8601; a naive timestamp (no offset) is UTC, and `Z` is UTC. An empty `since` is no lower bound. An unparseable `since` is HTTP 400 `{"error": "invalid_since"}`. A record with an unparseable `received_at` is omitted when `since` is set and sorts last otherwise. `type=finds` is `job_find`; `type=messages` is `message`. `kind=<kind>` filters one kind and overrides `type`. There is no `user` query parameter. The credential is the user. `list_recent_finds` treats a missing or JSON-null `company` as empty, so a filter of `none` does not match it.

`list_items` and `list_recent_finds` return newest first.

Record text is untrusted. The server stores it and returns it. It does not render it, fetch URLs inside it, or treat it as instructions. Clients (Muse, the Grok Bot) must do the same. Webhook error logs store the record id and a short error, not the record body.

### Why JSONL, not SQLite, for now

The original inbox was JSONL, `sync_inbox.py` still writes JSONL on the box, and a user's file is something an operator can read. Volume here is job posts and short messages, so a locked rewrite on delete is cheap. The cost is that retention and deletion rewrite the file instead of running a SQL `DELETE`.

`InboxStore` in `muse_bridge/storage/base.py` is the seam: `append`, `list_records`, `delete_records`, `delete_user`, `purge_expired`, `user_ids`. `JsonlInboxStore` is the only implementation. A SQLite store can replace it without changing tools or the pull API. That replacement is a follow-up, not this change. SQLite would be the better default once many users delete and filter concurrently; the protocol is what makes that a storage swap.

### Isolation

* Storage path is `data/users/<user_id>/records.jsonl`. Listing user A opens only that file.
* User ids are validated before they are joined, and the resolved path must stay inside `users/`.
* The pull API and every tool take `user_id` from the `Principal`, never from a parameter.
* `delete_my_data` and `DELETE /api/inbox` call `delete_records` for that user only.
* `admin delete-user` removes that user's keys and directory.

Locks are advisory `flock`s, shared for reads and exclusive for append, rewrite, and key updates, so the admin CLI and the server can run at the same time.

### Retention and deletion

`MUSE_BRIDGE_RETENTION_DAYS` (default **90**) is the max age. `0` means keep records until someone deletes them. A day is 86400 seconds. Records whose `received_at` cannot be parsed are kept.

Purge does not run on a timer unless `MUSE_BRIDGE_PURGE_INTERVAL_SECONDS` is greater than 0 (default **0**). Run it from cron or by hand:

```bash
python -m muse_bridge.admin purge
python -m muse_bridge.admin purge --days 30
```

User deletion:

* MCP `delete_my_data` with `confirm` exactly `delete`. Optional `kind`, `before` (ISO time; older records go), and `ids`. Confirm alone deletes the caller's whole inbox.
* `DELETE /api/inbox?confirm=delete` with the same optional filters.
* `python -m muse_bridge.admin delete-user --user alice --yes` removes keys and the inbox. This is the operator path for a deletion request.

Old on-box files (`inbox/job_finds.jsonl`, `inbox/messages.jsonl`) are not migrated automatically:

```bash
python -m muse_bridge.admin import-legacy --user legacy --from ./inbox
```

`webhook_errors.jsonl` is skipped. Import does not call the webhook.

### Webhook

`MUSE_BRIDGE_WEBHOOK_URL` (fallback `NORI_WEBHOOK_URL`) receives each newly stored record as JSON. `MUSE_BRIDGE_WEBHOOK_SECRET` (fallback `NORI_WEBHOOK_SECRET`) is sent as `X-Bridge-Secret`. `X-Bridge-User` is the user id. Delivery is a daemon thread with a 10 second timeout. Failures append to `data/webhook_errors.jsonl` and do not fail the tool. The URL must be `http` or `https`. It is operator configuration; record content cannot change it.

Write tools are annotated `openWorldHint: true` when a webhook URL is set.

## Environment

`.env.example` is updated in a separate change. These are the variables this core reads. Copy what you need into `.env` (mode `0600`). Do not commit `.env` or `data/`.

| Variable | Default | Meaning |
|---|---|---|
| `MUSE_BRIDGE_DATA_DIR` | `MUSE_BRIDGE_INBOX` if set, else `./data` | Keys, per-user inbox, webhook errors |
| `MUSE_BRIDGE_INBOX` | unset | Legacy name for the data directory (Docker/Fly still set this) |
| `MUSE_BRIDGE_HOST` | `127.0.0.1` | Bind address |
| `MUSE_BRIDGE_PORT` | `8765` | Bind port |
| `MUSE_BRIDGE_SERVER_NAME` | `nori-muse-bridge` | MCP name and the `/healthz` `server` field |
| `MUSE_BRIDGE_SERVER_TITLE` | `Nori Muse Bridge` | MCP title |
| `MUSE_BRIDGE_INSTRUCTIONS` | generic bridge instructions | MCP instructions |
| `MUSE_BRIDGE_LEGACY_SINGLE_TOKEN` | off | Accept `MUSE_BRIDGE_TOKEN` as one user |
| `MUSE_BRIDGE_TOKEN` | empty | Shared secret, used only when legacy mode is on |
| `MUSE_BRIDGE_LEGACY_USER` | `legacy` | User id for that shared secret |
| `MUSE_BRIDGE_ALLOW_QUERY_KEY` | off | Deprecated `?key=` support |
| `MUSE_BRIDGE_PUBLIC_URL` | request origin | Public origin, no path, used as the OAuth resource base |
| `MUSE_BRIDGE_OAUTH_ISSUER` | empty | Authorization server issuer to advertise |
| `MUSE_BRIDGE_RETENTION_DAYS` | `90` | Max age; `0` disables expiry |
| `MUSE_BRIDGE_PURGE_INTERVAL_SECONDS` | `0` | Background purge period; `0` means admin/cron only |
| `MUSE_BRIDGE_WEBHOOK_URL` | falls back to `NORI_WEBHOOK_URL` | Optional fan-out |
| `MUSE_BRIDGE_WEBHOOK_SECRET` | falls back to `NORI_WEBHOOK_SECRET` | `X-Bridge-Secret` |
| `MUSE_BRIDGE_LIMITS_ENABLED` | off | Install `muse_bridge.limits` middleware |
| `MUSE_BRIDGE_URL` | empty | Used by `sync_inbox.py`, not by the server |
| `MUSE_BRIDGE_API_KEY` | empty | Preferred credential for `sync_inbox.py` |
| `MUSE_BRIDGE_SYNC_INBOX` | `./inbox` next to `sync_inbox.py` | Where the pull script writes |

`sync_inbox.py` sends `Authorization: Bearer` with `MUSE_BRIDGE_API_KEY` or, if that is unset, `MUSE_BRIDGE_TOKEN`. It writes `inbox/job_finds.jsonl`, `inbox/messages.jsonl`, and one file per other kind. It does not write into `MUSE_BRIDGE_DATA_DIR`.

## Follow-ups

* OAuth access-token verification (JWKS or introspection) and audience checks. The interface and metadata endpoint are the seam.
* SQLite `InboxStore` if rewrite-on-delete or cross-user purge stops being cheap.
* Rate limit and payload middleware in `muse_bridge.limits` (the hook is already in `create_app`).
* Scope-limited API keys (read-only keys) once OAuth scopes are real. The checks already exist.
* Dockerfile must copy the `muse_bridge` package, not only `server.py`. That image change is owned elsewhere.
* Privacy policy, terms, and marketplace submission stay with the product owner.
