# docs/mcp-findings.md — Drupal MCP server inspection (R4 planning input)

Date: 2026-10-01. Inspected live via an MCP client against the DrupalForge
sandbox (Drupal 11.4.8, PHP 8.5.7). Site stack relevant to the findings: AI
module with providers, AI Search (experimental) with vector indexes, Search
API, Drupal Canvas, Content obeys standard node access. The server exposed
32 tools in two families. Tool names below are as served by THIS site —
treat every name as site-specific configuration, never a constant.

## Family 1 — entity tools (the Dreachy-relevant surface)

| Tool | What it does |
|---|---|
| demo_search_index | Text search over a named Search API index. On this site: `content_vector` (SEMANTIC search over articles, news, pages), `image_database_index_vector` (semantic image library search), `programs` (keyword). Returns ids of form `entity:node/NID:LANG:CHUNK`; the same node can appear once per matching passage. Has `check_access` (default TRUE), paging, `min_score`, AND/OR conjunction. |
| demo_entity_list | List entities of a type, optional bundle filter and sort. |
| demo_entity_load_by_id | Load one entity by type + numeric id. Returns a HANDLE TOKEN (`{{entity:...}}`) plus type/bundle/id/revision. |
| demo_entity_field_values | Read field values of a loaded entity (takes the token). |
| demo_entity_field_value_definitions | Field schema for a type+bundle: every base and configured field. |
| demo_entity_metadata | Type/bundle/id/language/revision metadata (takes the token). |
| demo_entity_stub | Create a new UNSAVED entity (returns a token). |
| demo_field_set_value | Set one field on a token; RETURNS A NEW TOKEN — always use the newest. |
| demo_entity_revision_add | Start a new unsaved revision of a loaded entity. |
| demo_entity_save | Save a token (new entity or revision). |
| demo_system_status | Core/PHP/DB/cron status. ADMIN-ONLY: other accounts get access denied. |

## Family 2 — canvas tools (discovered-extras candidates, 21 tools)

Drupal Canvas page building: create/delete pages, add/move/delete components,
set props, bind props to entity fields, page templates (page_variants), set
homepage, site settings. Key property: every canvas write lands in an
AUTO-SAVE STORE and goes live only on canvas_publish_auto_saves — staged by
design, which matches Dreachy's draft-only and confirmed-assent rules. The
publish tool itself is the one that must never be auto-exposed.

## Mapping to Dreachy's Backend interface

| Backend method | MCP realisation | Shape |
|---|---|---|
| find_content | demo_search_index(index=content_vector) | 1 call. Semantic — an UPGRADE over JSON:API keyword search. Dedupe multi-chunk hits by NID. |
| get_article | demo_search_index → demo_entity_load_by_id → demo_entity_field_values | 3-call chain with token passing. No path-or-title lookup exists; resolve via search first. |
| get_recent_nodes | demo_entity_list (bundle filter, sort by created desc) | 1 call. Verify sort/filter parameters when building. |
| get_schema | demo_entity_field_value_definitions per discovered bundle | N calls (one per type). Bundle list source needs confirming (entity_list or config). |
| get_site_pulse | COMPOSE from demo_entity_list counts per type | demo_system_status is admin-only; do not depend on it for pulse. Accept a reduced pulse in MCP mode or document the admin requirement. |
| get_pending_nodes | demo_entity_list + status/moderation filter | UNVERIFIED: whether entity_list filters on status/moderation_state. If not, this is a gap — pending content may need the JSON:API path even in MCP mode, or a server-side addition. |
| create_draft | demo_entity_stub → demo_field_set_value (xN, chaining tokens) → demo_entity_save | Multi-call chain. The draft-only guarantee (status/moderation_state) must be set via field_set_value and VERIFIED after save, Dreachy-side — the server offers no draft-only mode. |

## Consequences for the R4 design

1. Tool names and index names are per-site. McpBackend needs a mapping layer
   (discovered where possible, configurable where not): interface method →
   tool name(s) + parameters. The `content_vector` index name, for example,
   is this site's choice.
2. New pattern the backend must support: MULTI-CALL CHAINS with handle-token
   passing, including the use-the-newest-token rule after every
   field_set_value. One Backend method may be several MCP calls; partial-
   failure handling (chain breaks mid-way) needs defining.
3. Access control: `check_access=TRUE` stays on for every search. The
   account Dreachy authenticates as governs visibility, same as JSON:API
   mode; never set check_access FALSE.
4. Writes: all R3 guarantees (draft-only, confirmed=true, verify-after-save)
   are enforced in Dreachy, not assumed from the server. Canvas's auto-save
   staging aligns with this philosophy but canvas tools remain allowlist-only
   extras; canvas_publish_auto_saves and canvas_set_homepage are never
   exposed without explicit installer opt-in.
5. Demo contrast to build toward: same spoken question in JSON:API mode vs
   MCP mode — the MCP answer is semantically ranked. This is the visible
   payoff of the backend swap.
6. Open verifications for the plan (each becomes a task step):
   - demo_entity_list filter/sort parameter surface
   - whether pending/unpublished filtering is possible server-side
   - auth: which account the MCP session runs as and how Dreachy's OAuth
     (or other credentials) attach to it; whether scopes map
   - transport details (endpoint path, session negotiation) and the exact
     module/version serving this — pin them in this doc when confirmed
   - error shapes for refused tools and broken chains

## Honest limits of this inspection

Done with an authenticated admin session via a chat client, not from
Dreachy's code. Tool PARAMETER behaviour (filters, sorts, error shapes) was
read from schemas, not exercised exhaustively. The server's module identity
and version were not confirmed from inside the site — first task of R4
closes that.

## Confirmed first-hand (2026-10-01, anonymous HTTP from the laptop)

Checked against <https://prod-cd9108-fb3c83-z06v8dnk979w.drupalforge.app/>
with plain, unauthenticated requests. These close part of §6; the rest are
R4 Task 1.

- **Server module: `mcp_server`.** `GET /mcp` answers `401` with
  `WWW-Authenticate: Bearer realm="mcp_server"` and a JSON-RPC 2.0 error body
  (`{"code":-32001,"message":"Authentication required"}`). The version isn't
  visible anonymously.
- **Endpoint: `/mcp`.** `/mcp/post`, `/mcp/sse` and `/_mcp` are 404.
- **Auth: standard MCP OAuth discovery.**
  - `/.well-known/oauth-protected-resource` (RFC 9728) names the site itself
    as the authorization server.
  - `/.well-known/oauth-authorization-server` (RFC 8414):
    - token endpoint `/oauth/token`, authorization endpoint
      `/oauth/authorize`, dynamic client registration at `/oauth/register`;
    - grants `authorization_code`, `client_credentials`, `refresh_token`;
    - client authentication `client_secret_post` or `client_secret_basic`;
    - PKCE `S256` or `plain`.
- **Scopes:** `demo:mcp:connect`, `demo:content:read`, `demo:content:write`,
  `demo:canvas:read`, `demo:canvas:build`, plus OpenID ones.
- **Bearer token placement:** `header`, `body` or `query`.
- **JSON:API is not enabled on this site** (`/jsonapi` → 404), so this site
  is MCP-only. A "JSON:API fallback" for pending content doesn't exist here.

## Verified from Dreachy (2026-10-01, R4 Task 1)

Run from the laptop by `scripts/live/mcp_probe.py` against
<https://prod-cd9108-f20348-j4f8nqnixxkb.drupalforge.app/> (the sandbox moved;
the anonymous checks above were repeated there with the same results).
Recordings are in `tests/fixtures/mcp/`; file numbers below refer to
`responses/NN-*.json`. No access token is recorded anywhere.

### Pinned versions

- **Server:** `serverInfo` "Drupal MCP Server" **1.0.0**, protocol
  **2025-11-25**, capabilities completions, logging, prompts, resources,
  tools. It sends `instructions` ("Never publish, delete or change anything
  the user did not ask about…").
- **Client:** `mcp` **2.0.0** on `httpx2` **2.10.0**.
- **Tools:** 32, named `tool_api__demo_*` (11) and `tool_api__canvas_*` (21).
  The findings' short names are suffixes of the served names, so suffix
  discovery works.

### Auth (§6 "which account")

- `client_credentials` with scope `demo:mcp:connect demo:content:read
  demo:content:write` is granted (`expires_in` 300). The token is a JWT:
  `aud` is the client id, no `sub`.
- **The consumer runs as uid 1 (admin).** `system_status`, admin-only per
  this doc, succeeds (10), and the probe draft's author is `admin`, uid 1
  (36-uid). The scopes gate which tools are reachable, not the account's
  privilege: every read is an admin read, `check_access` included.
  **Least-privilege fix (site-side): point the consumer at a dedicated
  editor account.**
- A bad or expired token gets **HTTP 401 with an HTML body** from Simple
  OAuth (`WWW-Authenticate: Bearer realm="OAuth", error="access_denied"`), not
  the module's JSON-RPC -32001 (that one is only for a missing token). The
  SDK surfaces it as `MCPError -32603` "Server returned an error response"
  (13), so the 401 must be caught at the HTTP layer.

### Result envelope and error shapes (§6)

- Every tool answers `structuredContent` `{"success", "message", "data"}`.
- **Failures are `success: false` with `isError: false`** — the MCP error
  flag is never set. Examples: "Tool plugin access denied." (02, 03, 11, 40),
  "Entity validation failed: …" (save), "Field validation failed: …"
  (47; HTML-escaped placeholders in the message).
- A non-existent entity id is also "Tool plugin access denied." (11), so
  "missing" and "forbidden" can't be told apart.
- Unknown tool → JSON-RPC error "Tool not found" (09). A wrong argument type
  → JSON-RPC error "Invalid parameters for tool …: Property '/value': Invalid
  type…" (44). Both arrive as `MCPError`.

### Annotations

- **Every tool says `readOnlyHint: false`, reads included.** Read-only can't
  be detected from annotations: Dreachy must treat every unmapped tool as a
  write.
- `destructiveHint: true` on `canvas_delete_component`, `canvas_delete_page`,
  `canvas_discard_auto_save`. `canvas_publish_auto_saves` and
  `canvas_set_homepage` say `destructiveHint: false`, so they can only be
  denied by name.

### Parameters as served

| Tool | Arguments |
|---|---|
| entity_list | `entity_type_id` (required), `bundle`, `amount` (0 = all), `offset`, `sort_field`, `sort_order` (ASC/DESC), `fields` (string) |
| search_index | `index`, `search_words` (required), `amount`, `page`, `check_access`, `conjunction`, `fields`, `min_score`, `output_format` (markdown/rendered), `view_mode` |
| entity_load_by_id | `entity_type_id`, `entity_id` (integer) |
| entity_field_values | `entity` (handle), `fields` |
| entity_field_value_definitions | `entity_type_id`, `bundle` |
| entity_stub | `entity_type_id`, `bundle`, `base_fields` (object) |
| field_set_value | `entity`, `field_name`, `value` (object) |
| entity_save | `entity` |
| entity_metadata | `entity`, or `entity_type_id` + `entity_id` |
| entity_revision_add | `entity`, `default`, `revision_log` |

### Reads

- **entity_list** (§6 filter/sort surface): `data.results[]`, each
  `{"_metadata": {bundle, id, label, type, uuid}}`; the message carries the
  total ("out of a total 141"). Bundle filter and sort work (14, 15).
  **There is no status or moderation filter.** `fields` takes **one** field
  name: `"status"` adds `"status": "1"` (17), `"moderation_state"` adds
  `"published"`, `"draft"` or `null` for unmoderated bundles (18). A
  comma-separated list is "access denied" (02); a JSON-array string is
  ignored (16).
- **Pending (§6):** not filterable server-side, but ONE call —
  `entity_list` sorted by `changed` DESC, `amount` 20, `fields`
  `moderation_state` — returns the 20 latest items with their state (18,
  3.6 s). Ruling 4's option (a) costs 1 call, not 2N.
- **search_index:** `data.results[]` with `id`
  (`entity:node/NID:LANG:CHUNK`), `index`, `label`, `score`, `snippet`, `url`,
  and `fields` (`title`, `type`, `description`, `preview`, … each
  `{label, values: [...]}`) (04). One node appears per matching chunk and
  per language (`node/5:en`, `node/5:es`): dedupe by NID, keep the site
  language. The hit already carries title, type and a summary, so
  `find_content` needs no load per hit.
- **entity_load_by_id:** `data.loaded_entity` is a **string**: `"Entity
  object handle token: {{entity:47c0cb}}. Entity metadata: {json}"`, the
  JSON holding `entity_type`, `bundle`, `id`, `langcode`, `revision_id`
  (05). Stub, set and save answer the same way under `created_entity`,
  `updated_entity`, `saved_entity`.
- **entity_field_values:** `data.field_values`, a flat dict (06; 41 fields
  on a standard_page). Formatted text is the **value string only** (no
  format, no `processed`); `created`/`changed` are unix-timestamp strings;
  `status` is `"0"`/`"1"` on a saved entity but `true` on a stub (42b);
  references are `{"entity": {id, label, type}, "target_id"}` (36-uid).
  `fields` takes one name (12) — a list returns nothing (07, 07b).
- **entity_field_value_definitions:** `data.base_field_definitions` (29 on
  node) and `data.field_definitions`, each `{name, label, type, required,
  cardinality, settings, translatable, …}` (08, 20, 49). Text fields are
  found by type (`text_with_summary`, `text_long`).
- **Bundle list (§6):** `entity_list` on `node_type` is access-denied (03),
  as is `workflow` (40). `entity_list` for all nodes (`amount` 0) gives
  every bundle **that has content** in one call (22: 142 items, 4.0 s,
  72 KB) — 11 of the 13 bundles; `article` and `page` have no content and
  are missed. JSON:API's index (now enabled read-only) lists all 13.

### Handles and chains

- **Handles survive across sessions** (12): a token from one session works
  in the next. One session per method stays the rule (Ruling 2); pooling is
  now allowed as an optimisation.
- **Handles are immutable snapshots, not retired ones.** `field_set_value`
  returns a new handle; the old one still works but shows the entity
  **before** the change (32 vs 33, 48). Using a stale handle doesn't fail —
  it silently drops earlier changes. The newest-token rule matters more,
  not less.
- `field_set_value` takes `value` as an **object**; `{"0": {...}}` is
  rejected as an array (44). Title: `{"value": "…"}` (43).

### Writes

- **The draft guarantee depends on the bundle's workflow.** A new stub is
  already `moderation_state: draft` (42). On `article` ("AI Pre-Moderation"
  workflow) there is **no draft→draft transition**, so a new article can't be
  saved at all: setting `draft` fails field validation (47, 50) and saving
  without setting it fails entity validation ("Invalid state transition from
  Draft to Draft"). On `news` and `standard_page` ("Editorial") draft→draft
  is valid (56, 62). `student_announcement` is unmoderated (`null`, any
  value accepted, 68–73): there, only `status` keeps it unpublished.
- **Text formats:** `body` with `format: basic_html` is "Tool plugin access
  denied" (44b) — odd for uid 1, so the format may simply not exist here;
  the site's format list isn't reachable over MCP. No format, or
  `plain_text`, is accepted (45, 46). Dreachy writes notes as plain text
  with no format.
- **Required fields block a save.** standard_page requires `description`
  and `preview_text` (`ai_automator_status` defaults to `finished`). There's
  no `body` on most bundles here: the body field is per-bundle.
- **The probe draft:** node **142**, standard_page, "Dreachy MCP probe
  2026-10-01 14:19": stub (title, `status: false`) → set `description` →
  set `preview_text` → save → reload. Read back: `moderation_state`
  `draft`, `status` `"0"` (35, 36). **It's still on the sandbox; delete it
  when convenient** (Dreachy never deletes).

### Latency (§6 timing)

- A session with `initialize` + `tools/list`: **≈4 s**.
- Individual calls: 0.4–1.5 s typically; search 3.5 s, load 2–4 s, save
  0.5–5.4 s.
- One session of 11 read calls: 22 s; the write chain (stub, 2 sets, 2
  reads, save): 14 s.
- So a spoken `get_article` (search → load → values) is ≈10 s with session
  setup, against well under a second over JSON:API. Pooling (now allowed)
  saves the 4 s setup.

### Added while starting Tasks 4–8 (2026-10-01)

- **`fields` takes exactly one name**, on `entity_list` and
  `entity_field_values` alike: `"a,b"`, `"a, b"` and `"a b"` all return
  items with no fields at all (no error).
- A field a bundle doesn't have is simply absent from its items (24).
  `path` comes back as `{"alias", "pid", "langcode"}` (23).
- **Parallel calls in one session work, up to about 3 at once.** Six list
  calls took 9.1 s one after another, and 4.0–5.3 s three at a time. Six at
  once took 2.8 s, but two of them got **HTTP 503** "The website encountered
  an unexpected error", which the SDK reports as -32603 (25, 26).
- Field definitions carry `required` and a `value_schema`, but **no default
  value**. Every bundle here requires `ai_automator_status` (`list_string`),
  which the site fills in on save (`finished`, 36).
- The search tool's description names its indexes in prose ("pass index
  content_vector…"), and its `index` parameter has no `enum`.
