# Tools

All tools run as the authenticated user. They cannot name another user. Record text is untrusted data: list tools return it, and nothing in this server follows instructions inside it.

Annotations are MCP hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`). Clients may ignore them. `openWorldHint` is true on write tools only when `MUSE_BRIDGE_WEBHOOK_URL` (or `NORI_WEBHOOK_URL`) is set, because those calls POST the new record to that URL.

String fields are truncated in the tool. A separate payload-size middleware can reject the HTTP body before this runs; that middleware is not part of this package yet.

Limits below are enforced here:

| Limit | Value |
|---|---|
| Job finds per `report_job_finds` call | 100 (the call is rejected, nothing is stored) |
| `list_*` limit | 1..200 (values outside that clamp) |
| `title` | 300 |
| `company`, `location` | 200 |
| `url` | 2000 |
| `posted` | 100 |
| `notes`, generic payload strings | 4000 |
| `batch_note` | 2000 |
| `source` | 200 |
| `subject` | 300 |
| `text` | 20000 |
| `urgency` | 40 |
| Generic payload | JSON values, depth 6, 100 keys or items per container, 32 KB encoded |
| `kind` | `^[a-z][a-z0-9_]{0,31}$` |
| `delete_my_data` ids | 500 |

Validation errors are returned in the tool result (for example `errors[]` on `report_job_finds`, or `error` on `submit_item`). A missing credential never reaches a tool. A token without the required scope raises a tool error `missing scope: inbox:read` or `inbox:write`.

## ping

Read. No side effects.

| | |
|---|---|
| Annotations | read-only, idempotent, not destructive, closed world |
| Scope | any authenticated user |
| Inputs | none |
| Output | `{"ok": true, "server", "version", "time"}` |
| Errors | unauthenticated requests never call the tool |
| Side effects | none |

## submit_item

Write. Additive. Not idempotent, except when a `dedupe_key` repeats or `kind` is `job_find` (jobs have their own dedupe).

| | |
|---|---|
| Annotations | not read-only, not destructive, not idempotent; open-world if a webhook is set |
| Scope | `inbox:write` |
| Inputs | `kind` (string), `payload` (object), `source` (default `muse`), `dedupe_key` (default empty) |
| Output | `{"stored": bool, "id", "kind", "error", "skipped"?}` |

`kind=job_find` requires `payload.title`, `payload.company`, and `payload.url`. Optional: `location`, `posted`, `notes`, `batch_note`. Duplicates return `stored: false` and `skipped: "duplicate"`. The stored record is the flat job shape, not a nested payload.

`kind=message` reads `payload.text`, `payload.subject`, and `payload.urgency` (default `normal`).

Any other kind stores `payload` as JSON on the record.

Errors: `invalid_kind`, `payload must be an object`, `title, company and url are required`, plus the payload limit errors (`payload is too large`, `payload nested too deeply`, `payload list is too long`, `payload object is too large`, `payload has a non-finite number`, `payload must be made of JSON values`).

Side effects: one new inbox record, and a webhook POST when configured. A duplicate does not write and does not call the webhook.

## list_items

Read. No side effects.

| | |
|---|---|
| Annotations | read-only, idempotent, not destructive, closed world |
| Scope | `inbox:read` |
| Inputs | `kind` (default all), `limit` (default 20, max 200), `since` (ISO time, exclusive; default none). Compared as absolute time. A naive timestamp is UTC. `+00:00` and `-05:00` are offsets, not text. An unparseable `since` returns `error: "invalid_since"`. |
| Output | `{"count", "items"}` newest first. Full stored records. |
| Errors | `invalid_kind` returns `{"count": 0, "items": [], "error": "invalid_kind"}` |

## send_message

Write. Each call appends a message unless `dedupe_key` is set and already stored. The MCP tool does not expose `dedupe_key`; repeated calls add records. `submit_item` with `kind=message` can pass `dedupe_key`.

| | |
|---|---|
| Annotations | not read-only, not destructive, not idempotent; open-world if a webhook is set |
| Scope | `inbox:write` |
| Inputs | `text`, `subject` (default empty), `urgency` (default `normal`), `source` (default `muse`) |
| Output | `{"stored": "<id>"}` |
| Side effects | one `message` record and an optional webhook |

`urgency` is free text, truncated to 40 characters. Empty text is stored (compatible with the original tool).

## report_job_finds

Write. Compatible with the original tool. Idempotent for the inbox: the same URL (or title+company) is stored once per user.

| | |
|---|---|
| Annotations | not read-only, not destructive, idempotent; open-world if a webhook is set |
| Scope | `inbox:write` |
| Inputs | `finds` (list of objects), `source` (default `muse`), `batch_note` (default empty) |
| Output | `{"stored": [ids], "skipped": [{index, url, reason}], "errors": [{index, error}], "inbox"}` |

`inbox` is the logical path `users/<user_id>/records.jsonl`, not a host filesystem path.

Each find needs `title`, `company`, and `url`. Optional: `location`, `posted`, `notes`.

Errors inside `errors`: `not an object`, `title, company and url are required`. The whole call returns one error and stores nothing when `finds` is not a list or contains more than 100 items. `finds` omitted or null is an empty batch.

Skipped duplicates use `reason: "duplicate"`. Ids look like `find_<8 hex>_<index>`.

Side effects: one record per new find, then a webhook per record. Duplicates and invalid rows do not write.

## list_recent_finds

Read. Compatible with the original tool.

| | |
|---|---|
| Annotations | read-only, idempotent, not destructive, closed world |
| Scope | `inbox:read` |
| Inputs | `limit` (default 20, max 200), `company` (case-insensitive substring, default empty). A missing or JSON-null `company` is empty, so the filter `none` does not match it. |
| Output | `{"count", "finds"}` newest first. Each find has `id`, `received_at`, `title`, `company`, `location`, `url`, `posted`, `status`. |
| Side effects | none |

## send_message_to_nori

Write. Compatible alias of `send_message` with `source` fixed to `muse`. The name stays so existing Muse connectors keep working. New connectors should call `send_message`.

| | |
|---|---|
| Annotations | not read-only, not destructive, not idempotent; open-world if a webhook is set |
| Scope | `inbox:write` |
| Inputs | `text`, `subject` (default empty), `urgency` (default `normal`) |
| Output | `{"stored": "<id>"}` |
| Side effects | one `message` record and an optional webhook |

## delete_my_data

Destructive. Idempotent: deleting the same records again removes nothing further. Only the caller's inbox is affected.

| | |
|---|---|
| Annotations | not read-only, destructive, idempotent, closed world |
| Scope | `inbox:write` |
| Inputs | `confirm` (required), `kind` (default empty), `before` (ISO time, default empty), `ids` (default none) |
| Output | `{"deleted": n}` or `{"deleted": 0, "error", "hint"?}` |

`confirm` must be the exact string `delete`. Anything else returns `error: "confirmation_required"` and deletes nothing. With only that confirm, every record for this user is deleted. `kind` limits the kind. `before` deletes records older than that timestamp; records with an unparseable `received_at` are kept. `ids` deletes those ids if they belong to the caller. Ids of another user are not visible, so they do not match.

Other errors: `invalid_kind`, `invalid_before`, `too_many_ids` (more than 500).

No webhook. The HTTP equivalent is `DELETE /api/inbox?confirm=delete`.

## HTTP pull API (not an MCP tool)

`GET /api/inbox` requires `inbox:read`.

Query: `since` (ISO-8601, exclusive, absolute time; a naive timestamp is UTC; default empty keeps every record), `type` (`all`, `finds`, or `messages`; default `all`), `kind` (overrides `type`). An unparseable `since` is HTTP 400 `{"error": "invalid_since"}`. When `since` is set, a record whose `received_at` cannot be parsed is left out. Results are oldest first by that instant; an unparseable `received_at` sorts last.

Output: `{"count", "records"}` oldest first, the caller's records only.

`DELETE /api/inbox` requires `inbox:write` and `confirm=delete`. Same filters as `delete_my_data`: `kind`, `before`, and repeated `id`. Missing confirm is HTTP 400 `confirmation_required`. A bad `type` or `kind` on GET is HTTP 400.

Both return HTTP 401 `{"error": "unauthorized"}` with no credential, and HTTP 403 `{"error": "forbidden"}` when the scope is missing.
