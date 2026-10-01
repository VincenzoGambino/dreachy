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
