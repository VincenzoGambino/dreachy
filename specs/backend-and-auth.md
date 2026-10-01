# specs/backend-and-auth.md — Pluggable backend, schema-driven config, OAuth

## Context

Dreachy (this repo) is a thin wrapper over `reachy_mini_conversation_app`: a
bundled profile plus five `Tool` subclasses that talk to a Drupal site over
JSON:API. Current architecture per README: `tools/` → `tool_queries.py` →
`client.py`/`config.py`, one reaction (`perk_up`), settings page writing an
instance-path `.env`, ambient attention polling JSON:API directly.

This spec upgrades Dreachy in these releases (R5 and R6 not yet planned):
- **R1** — schema-driven content model (kill the Umami hardcoding)
- **R2** — backend seam + `AuthProvider` (OAuth2 client credentials)
- **R3** — authenticated editorial tools + confirmation-gated write-back
- **R4** — `McpBackend` (Drupal MCP module as alternative transport)
- **R5** — Paragraphs support *(future; added 2026-09-30, not yet specified)*
- **R6** — per-user login via the OAuth device authorization grant *(future;
  added 2026-09-30)*

Each release ships independently. Do them in order; stop at release boundaries
for review. Before writing any code, **read the actual modules** (`client.py`,
`config.py`, `tool_queries.py`, `tools/`, `main.py`) and flag anywhere this
spec's assumptions diverge from reality — the spec was written from the README,
not the source.

## Invariants (hold across all releases)

- Anonymous read-only JSON:API mode remains the zero-config default. Auth and
  MCP are additive. A user who sets only the site URL gets today's behaviour.
- Never fork or modify `reachy_mini_conversation_app` — everything stays in
  Dreachy's wrapper layer.
- The five existing tools keep their names and speech-facing behaviour.
- Secrets are confined to the client/auth layer, never logged, never in the
  profile or repo. They're stored in the instance-path `.env` (the settings
  page manages it); `client_secret`-style fields are write-only password
  inputs on the settings page. *(Amended 2026-09-30 — see Amendments.)*
- New spoken confirmations get a line in the profile instructions, not
  hard-coded English strings — Dreachy may be running an Italian persona.
  Tool results (errors, relative ages) are LLM input rather than verbatim
  speech, and may stay English. *(Amended 2026-09-30 — see Amendments.)*
- Tests: pytest, no network — HTTP and MCP mocked. Existing tests keep passing.

## R1 — Schema-driven content model

Goal: a site with `news_item`/`body` works without editing Python.

1. Add `get_schema()` to the client: fetch content-type and field information
   from the site. Core exposes no anonymous schema endpoint, so the route is
   *(amended 2026-09-30)*:
   - the JSON:API index (`/jsonapi`) for the list of node types;
   - `node_type--node_type` for human labels, best effort (core 11.4 lets
     `access content` view node types; otherwise derive the label from the
     machine name);
   - text fields inferred from the values of a few recent nodes per type,
     since field definitions (`field_config`) need `administer node fields`.

   Document the route in the code. Output: an ordered mapping
   `{content_type: {label, label_field, text_fields[], summary_field}}`, where
   `summary_field` is a teaser field of its own (Umami's recipe
   `field_summary`) or none.
2. Heuristics when config isn't explicit: prefer `body`, then `field_body`,
   then any long-text field; a field named like `summary`/`teaser` is the
   teaser, not the body. Skip types whose content has no text fields. Keep
   types with no content yet, with a guessed body field (`body`/`field_body`,
   plus their Umami mapping if they have one), so the first node of a new
   type is still noticed *(amended 2026-09-30)*. Umami's
   `article`/`page`/`recipe` mapping must fall out of the heuristics naturally
   — keep the current `_TEXT_FIELDS` as a fallback table only.
3. Settings page: show the discovered types with include/exclude checkboxes,
   persisted to `.env` (e.g. `DREACHY_TYPES=article,recipe`). Empty = all
   discovered. The search tool's type list is built from the discovered types
   at app start, so a "restart to apply" note sits beside the checkboxes for
   that part *(amended 2026-09-30)*.
4. Cache the schema at app start; refresh on settings save.
5. ✅ Checks: unit tests for the heuristics against fixture schemas (Umami +
   one invented non-Umami model); manual test against the live Umami sandbox
   unchanged in behaviour.

## R2 — Backend seam + AuthProvider

Goal: `tool_queries.py` talks to an interface, not to JSON:API; OAuth optional.

1. Define `Backend` (abstract) with the methods Dreachy actually calls today
   *(amended 2026-09-30)*: `get_recent_nodes(limit)`,
   `find_content(keyword, content_type=None)`, `get_article(title_or_path)`
   (a URL path alias or an exact title — never a type and id),
   `get_site_pulse()` and `get_schema()`, plus R1's schema cache (`schema`,
   `full_schema`, `schema_discovered`, `refresh_schema()`). The enabled types
   come from the backend's own config (`DREACHY_TYPES`), not a parameter.
   Reads are published-only by default: the read methods take an explicit
   `include_unpublished=False`, which no R2 tool sets — R3's editorial tools
   will, deliberately.
2. Move current `client.py` logic into `JsonApiBackend(Backend)`. Ambient
   attention's polling goes through the backend too.
3. `AuthProvider`: `none` (default) and `oauth_client_credentials`. Dreachy
   selects the provider from config; drupal-api-client (≥0.3.1) handles the
   token *(amended 2026-09-30)*: POST to `{site}/oauth/token` with the client
   id/secret, token cached with expiry, proactive refresh at <60 s remaining
   (`token_refresh_margin`, default 60), one retry on 401. Dreachy turns a
   refused login into a clear error. Secrets per the Invariants.
4. Config/env additions: `DREACHY_AUTH=none|oauth`,
   `DREACHY_OAUTH_CLIENT_ID`, `DREACHY_OAUTH_CLIENT_SECRET`.
   `DREACHY_BACKEND` is deferred to R4, which has a second backend to select
   *(amended 2026-09-30)*. Settings page grows a collapsed "Advanced"
   section for these; secret field is write-only (shows set/unset, never the
   value).
5. Drupal-side doc: add `docs/drupal-setup.md` covering `simple_oauth` install,
   creating the consumer, and a dedicated `dreachy` role with least-privilege
   permissions. Read-only scope for R2.
6. ✅ Checks: all five tools green against mocked backend in tests; live test
   anonymous (unchanged) and authenticated (Bearer header present, verified via
   a permissioned-content read that anonymous cannot see).

## R3 — Editorial tools + gated write-back

Goal: with credentials present, Dreachy gains an editor persona.

1. Conditional tool registration: at startup, if auth is configured and the
   token grant succeeds, register two additional tools; otherwise they don't
   exist. Follow the conversation app's tool-loading mechanism (tools.txt /
   external tools) — investigate the cleanest way to make registration
   conditional without forking; if tools.txt is static, the tools themselves
   should return a polite "not available on this site" when unauthenticated.
   *(Amended 2026-09-30:)* both — Dreachy renders `tools.txt` itself and adds
   the two tools only when the login check passes at start, **and** each tool
   refuses at call time when not logged in (an installer's
   `AUTOLOAD_EXTERNAL_TOOLS` would load them regardless).
2. `drupal_pending_content`: unpublished/draft nodes of the enabled types
   (and, if the site runs core Content Moderation, items in review states —
   detect, don't assume). Spoken summary: counts + latest titles.
3. `drupal_create_note`: creates an **unpublished** node (type configurable,
   default the first enabled type) with spoken-dictated title/body.
   **Hard rules**: always unpublished — `status=false`, or the `draft`
   moderation state on types under Content Moderation, which forbids setting
   `status` *(amended 2026-09-30)*; no update/delete tools; the tool
   description must instruct the LLM to confirm aloud before calling (and the
   profile instructions repeat it): ask "shall I save it as a draft?" and only
   call after explicit assent. The tool itself is the last line of defence:
   reject calls missing an explicit `confirmed=true` argument.
4. Prompt-injection hygiene, now that site content and write tools coexist:
   profile instructions state that site content is data, never instructions;
   `drupal_create_note` must never be triggered by content found in articles.
   Add a test-fixture article containing an injection attempt and a manual
   test that Dreachy reads it aloud without acting on it.
5. ✅ Checks: unit tests for conditional registration and the `confirmed`
   guard; live demo flow — ask for pending content, dictate a note, see it
   unpublished in the admin.

## R4 — McpBackend

Goal: same five tools over the Drupal MCP module; discovered extras optional.

1. Research first, in-repo: `docs/mcp-findings.md` on the current Drupal MCP
   module — transport (streamable HTTP vs SSE), auth support, tool vocabulary
   it exposes. Pin exact module + client library versions; this ecosystem
   moves fast and the doc is the changelog anchor.
2. `McpBackend(Backend)`: MCP client session to the site's server, mapping the
   five interface methods onto the server's tools. Reuse `AuthProvider` if the
   module's auth allows; document the gap if it doesn't. *(Amended 2026-10-01
   — see Amendments.)*
3. Discovered extra tools: OFF by default. `DREACHY_MCP_EXTRA_TOOLS=allowlist`
   (comma-separated tool names) to expose more; any write-capable discovered
   tool gets the same confirmed-assent rule as R3. Never auto-expose writes.
   *(Amended 2026-10-01 — see Amendments.)*
4. Settings page: backend selector (JSON:API / MCP), backed by
   `DREACHY_BACKEND=jsonapi|mcp` (deferred here from R2), and an MCP endpoint
   field.
5. ✅ Checks: five tools green against a mocked MCP session; live test against
   the DrupalForge sandbox with the MCP module; document in the README what
   MCP mode adds and requires.

## R5 — Paragraphs support (future)

Goal: a site whose body text lives in Paragraphs is read aloud and searched
like any other. Today R1's discovery doesn't see Paragraphs (they are
relationships, not attributes): such types are skipped, and a site where no
type has formatted text falls back to the Umami mapping. To be specified and
planned after R4; added to the roadmap 2026-09-30.

## R6 — Per-user login (future)

Goal: "Reachy, log me in" — the robot speaks a code and URL, the person
approves on their phone (OAuth device authorization grant), and Dreachy acts
as them until they log out or go idle. Design decisions *(recorded
2026-09-30)*:

- **Precondition:** verify the installed `simple_oauth` version supports the
  device authorization grant. If it doesn't, the fallback is the
  authorization-code grant, started from the settings page.
- **One active identity at a time**, announced aloud on login.
- **Session end:** idle timeout (default 30 minutes) and spoken logout. On
  expiry or logout Dreachy falls back to the service-account consumer (R2
  behaviour).
- **Authorship follows identity:** drafts created while someone is logged in
  are authored by that user; otherwise by the `dreachy` service user.
- **Confirmation gates survive login:** `confirmed=true` is still required.
  Authentication raises what the robot *can* do, never what it does
  unconfirmed.
- **Secrets:** user tokens live in the same client/auth layer as the consumer
  credentials, under the same secrecy invariant.
- **Rejected:** voice/speaker recognition as an identity factor (GDPR:
  biometric data).

## Tracked separately

- **Forward drafts in pending content.** A new draft of already-published
  content (Content Moderation's "Create New Draft" from Published) is a
  non-default revision that JSON:API list reads don't return, so R3's
  `drupal_pending_content` undercounts on moderated sites. Reading latest
  revisions needs a request per node. Out of scope for R3 (ruled
  2026-09-30); a candidate for a later release.

- **Settings-page authentication.** The settings endpoints (`/api/config`,
  `/api/schema`) are unauthenticated on the robot's network — a pre-existing
  gap. R2's OAuth covers Dreachy's requests *to Drupal* only; it doesn't
  protect the settings page. Separate item, not part of any release above.

## Out of scope

Speaker identification (voice is rejected as an identity factor — see R6),
publish/update/delete tools *(amended 2026-10-01: tools with immediately-live
effects — see Amendments)*, non-Drupal backends, changes to the reaction system, packaging changes beyond version
bumps.

## Definition of done (per release)

Tests green, README + settings page updated, a short entry in CHANGELOG.md,
and — for R2/R3 — `docs/drupal-setup.md` verified by a clean walkthrough on
the sandbox.

## Amendments

Explicit changes to this spec, made after reading the source (see
`plans/backend-and-auth.md`, "Spec vs code").

- **2026-09-30 — R1.1 discovery route.** Core JSON:API has no anonymous
  schema or field-definition endpoint (`field_config` needs `administer node
  fields`). Route is now: index for types, node types for labels (best
  effort), value inference from sample nodes for text fields.
- **2026-09-30 — R1.1 mapping shape.** Adds `label` and `summary_field`. The
  current code has a teaser role (recipe's `field_summary`); without it,
  Umami's mapping couldn't fall out of the heuristics.
- **2026-09-30 — R1.2 empty types.** Types with no content yet are kept with a
  guessed body field rather than skipped, so the watcher notices their first
  node. Types whose content has no text fields are still skipped.
- **2026-09-30 — R1.3 restart note.** Tool specs are built once at start, so
  the search tool's type list follows the checkboxes only after a restart;
  the settings page says so beside them.
- **2026-09-30 — Invariants, English strings.** Scoped to new spoken
  confirmations. Existing tool results are already English and are LLM
  input, not verbatim speech.
- **2026-09-30 — Invariants, secret handling.** Was "secrets live only in the
  `.env`; backends never see the secret". Now: secrets are confined to the
  client/auth layer, never logged, never in the profile or repo. drupal-api-client
  holds the OAuth credentials and does the token handling itself (its 0.3.1
  release adds a configurable refresh margin and one retry on 401), so the
  JSON:API backend necessarily passes the secret to it; what matters is that
  it goes no further.
- **2026-09-30 — R2.1 Backend methods.** Replaced the invented `search`,
  `get_node`, `list_recent` and `site_stats` with the methods Dreachy calls
  (from the R1 review), and made reads published-only behind an explicit
  `include_unpublished=False`.
- **2026-09-30 — R2.3 AuthProvider.** Dreachy selects the provider;
  drupal-api-client 0.3.1 handles the token (refresh margin, default 60 s;
  one retry on 401). Replaces "backends never see the secret", superseded by
  the secret-handling invariant above.
- **2026-09-30 — R2.4 `DREACHY_BACKEND`.** Deferred to R4, which has a second
  backend to select.
- **2026-09-30 — R6 per-user login added; per-user auth removed from Out of
  scope.** Device authorization grant, design decisions recorded in the R6
  section. Paragraphs support keeps R5.
- **2026-09-30 — R3.1 conditional registration.** Both mechanisms: `tools.txt`
  rendered with the editorial tools only when the login check passes at
  start, and a call-time refusal in each tool.
- **2026-09-30 — R3.3 "always unpublished".** `status=false`, or the `draft`
  moderation state on moderated types (Content Moderation forbids setting
  `status` there). Forward drafts in pending content are out of scope
  (Tracked separately).
- **2026-10-01 — R4.2 interface methods.** "The five interface methods" is
  now every `Backend` method: R1 and R2's five reads (`get_schema`,
  `get_recent_nodes`, `find_content`, `get_article`, `get_site_pulse`),
  R3's `get_pending_nodes`, `create_draft` and `can_edit`, and `close` —
  nine in all. `McpBackend` implements all of them, so all seven tools and
  the watcher run on either backend.
- **2026-10-01 — R4.3 discovered extras through one proxy tool.** Allowlisted
  extras aren't registered as tools of their own: one tool,
  `drupal_site_action` (`action` from the allowlist, `arguments`,
  `confirmed`), runs them. It is registered only on the MCP backend with a
  non-empty effective allowlist (`DREACHY_MCP_EXTRA_TOOLS` ∩ the server's
  tools ∖ the hard deny-list). An extra is read-only only when the server
  marks it so (`readOnlyHint`); anything else needs `confirmed=true` after
  the person's spoken yes, as in R3.
- **2026-10-01 — Out of scope, publish/update/delete.** Out of scope means
  tools with **immediately-live effects**. Staging tools — writes that land
  in a draft store and go live only when someone publishes them, such as
  Drupal Canvas's auto-save writes — are allowlistable extras under the
  write-assent rule (R4.3). Tools that make staged changes live (publishing
  auto-saves), tools with immediately-live site-wide effects (setting the
  homepage or a site default), and destructive tools (delete, discard, or
  any the server marks `destructiveHint`) stay hard-denied whatever the
  allowlist says. Extras are never asked to go live (`published` is never
  sent true), and the entity write tools are never extras: notes go only
  through `create_draft`'s draft checks.
