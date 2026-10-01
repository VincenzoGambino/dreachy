---
title: Dreachy
emoji: 💧
colorFrom: red
colorTo: blue
sdk: static
pinned: false
short_description: Reachy Mini becomes the voice and body of a Drupal site.
tags:
 - reachy_mini
 - reachy_mini_python_app
---

# Dreachy

Reachy Mini becomes the embodiment of a Drupal site. It isn't a robot that happens to know
about a website — it *is* the site's editorial voice, given a body:

- **What's new** — asks the site what's been published lately, and says so.
- **Find content** — searches the site by keyword instead of answering from its own training
  knowledge; if the site has nothing on a topic, it says so honestly.
- **Site pulse** — reports its own "mood" from real site activity (how much content exists, how
  recently it changed).
- **Read aloud** — reads any article, page, or recipe out loud in full.
- **Ambient attention** — watches the site in the background and reacts physically — antennas
  flare, a little head lift — the moment something new goes live, unprompted.

Dreachy is a thin wrapper: all the conversation, voice, and hardware plumbing comes from
[`reachy_mini_conversation_app`](https://github.com/pollen-robotics/reachy_mini_conversation_app);
this package only supplies its own persona (a bundled profile) and its tools, which talk to a
Drupal site over JSON:API or, in **MCP mode**, through the site's own MCP server (see below).

## Requirements

- A Drupal site with:
  - **JSON:API** enabled (core module, no extra configuration needed for read access).
  - The **language prefix** set correctly for the site (see Configuration): `en` for a
    multilingual site served at `/en`, blank for a single-language site.
  - **Anonymous read access** to the content Dreachy should talk about — or, for a private
    site, an OAuth client (see [`docs/drupal-setup.md`](docs/drupal-setup.md)). Dreachy only
    ever reads published content.
  - At least one content type with a formatted text field (core's `body`, for example). Dreachy
    reads the site's content types and their text fields itself — see **Content types** under
    Configuration — so any content model works without code changes. The **Umami demo
    profile**'s `article`/`page`/`recipe` mapping remains the built-in fallback for when the
    site can't be read.
- Or, for **MCP mode**: the site's `mcp_server` module and an OAuth client for it — see
  **MCP mode** below and [`docs/drupal-setup.md`](docs/drupal-setup.md) §9.
- A Reachy Mini, and a Hugging Face token for the realtime voice/LLM backend (same requirement
  as `reachy_mini_conversation_app` itself).

## Installing

From the Reachy Mini dashboard's app store, search for **Dreachy** and install it — or install
manually:

```bash
pip install git+https://github.com/VincenzoGambino/dreachy
```

## Configuration

Dreachy needs to know which Drupal site to embody. Once installed, open its settings page from
the Reachy Mini dashboard (the gear/settings icon next to the app) and set:

- **Drupal site URL** — takes effect immediately, no restart needed. It must be reachable
  *from the robot*, not just from your laptop (see Known issues).
- **Language prefix** — multilingual sites serve JSON:API under a language code
  (`/en/jsonapi/…`); enter that code. Leave it blank for a single-language site, which has
  no prefix. Defaults to `en`, matching the Umami demo profile. Takes effect immediately.
- **Content types** — the site's content types as Dreachy found them, one checkbox each. Leave
  them all ticked to include every type, including ones the site adds later. Takes effect
  immediately, except the search tool's type filter: restart Dreachy to apply that, as the page
  notes beside the checkboxes. Saving also re-reads the site's types, so a type created on the
  site since start shows up after a save.
- **Advanced: site login** (optional) — OAuth client credentials for a site whose content
  isn't public; see [`docs/drupal-setup.md`](docs/drupal-setup.md). The secret is write-only:
  once saved it's never shown again, and a blank field keeps it. Takes effect immediately.
  Changing the site URL removes the saved login, so it's never sent to a different site.
  With the login working, Dreachy can also list pending content and save dictated notes as
  unpublished drafts; choose the note type there too (see `docs/drupal-setup.md` §8).
  The same section chooses the backend — JSON:API (default) or the site's MCP server — and
  holds MCP mode's settings (see **MCP mode**).
- **Extra instructions** (optional) — free text appended to Dreachy's built-in persona (tone,
  language, anything else). Built-in guardrails (e.g. "only answer from site content") stay in
  force either way, since this is appended, not a replacement. Takes effect the next time the
  app starts.

No SSH needed for any of those. `HF_TOKEN` (needed for the realtime voice/LLM backend) is
handled separately by the robot's own Hugging Face sign-in in the dashboard — not something
Dreachy's own settings page touches.

If you'd rather configure it by hand (e.g. scripting a fresh install), these settings are backed
by a `.env` file at `~/.local/share/dreachy/.env` on the robot — see
[`.env.example`](.env.example).

## MCP mode

Dreachy can talk to the site through the site's own MCP server (the Drupal `mcp_server`
module) instead of JSON:API. Choose it under **Advanced** on the settings page. Search, the
watcher, notes and pending work as before; three things change:

- **Search by meaning.** "Find content" uses the site's Search API index (often a vector
  index), so it finds what a question is *about*, not only titles containing a word, and
  answers best match first rather than newest first.
- **MCP-only sites.** A site with JSON:API switched off can still be embodied.
- **Site actions** (optional). The installer may allow a few of the site's extra MCP tools
  (for example Drupal Canvas's) by name; Dreachy runs one only when asked, and asks "shall I
  go ahead?" before anything that changes the site. Anything that publishes, sets the
  homepage or a site default, deletes or discards is never available, whatever is listed —
  and the entity write tools are never extras: notes go only through their own draft checks.

**What it needs:** the `mcp_server` module at `/mcp` (or another address on the same site),
the site login (OAuth client credentials with the MCP scopes; `docs/drupal-setup.md` §9),
and the **search index** name the site's MCP search tool should use (for example
`content_vector`).

**Settings** (Advanced, or the instance `.env`): `DREACHY_BACKEND=mcp`;
`DREACHY_MCP_ENDPOINT` (optional, must be on the site's own address — the site login goes
with every request); `DREACHY_MCP_SEARCH_INDEX`; `DREACHY_MCP_BUNDLES` (optional content
types); `DREACHY_MCP_EXTRA_TOOLS` (optional site actions). `DREACHY_MCP_MAPPING`, a JSON
object, can name the site's tools when their names don't follow the usual pattern
(`.env` only). Changing the backend takes effect at once for reads; which tools exist
(notes, site actions) is decided when Dreachy starts.

**Limits of MCP mode:**

- **Paths are searched, not looked up.** MCP has no lookup by URL path or exact title:
  "read me /about-us" searches for "about us". An exact title among the results wins;
  otherwise Dreachy reads a result only if its title contains a word asked for — never just
  the nearest match.
- **Search covers what the index covers.** A content type outside the search index can't be
  found or read aloud, though "what's new" still lists it.
- **Content types with no content yet are invisible.** Dreachy finds the types from a listing
  of the site's content, unless you list them under **Content types (optional)**.
- **Pending looks at the 20 most recently changed items.** The MCP tools can't filter by
  status, so older drafts aren't counted.
- **Notes need every required field to be one Dreachy can fill.** It fills required text
  fields with the note's text and never invents any other value; when the site refuses the
  save it names the missing field aloud, and nothing is saved. By default notes go to the
  type that needs the fewest extra fields. A type whose workflow can't save a new item as a
  draft refuses notes the same way.
- **It's slower.** Each MCP call takes 0.5–3.5 seconds on the sandbox. Dreachy keeps one
  connection open for reads and makes at most three calls at a time, but reading an article
  aloud still takes several seconds.

## Known issues

- **The site must be reachable from the robot.** Dreachy runs on the Reachy Mini, not on your
  laptop, so a local development URL (`localhost`, `*.ddev.site`, a private hostname) won't
  resolve there. Use a publicly reachable site, a tunnel, or a hosting sandbox.
- **Content-type discovery reads real content.** Anonymous JSON:API can't see field
  definitions, so Dreachy finds a type's text by looking at its five most recent nodes. It takes
  a formatted text field as the body (`body` preferred, then `field_body`, then any other) and a
  field named like `summary` or `teaser` as the teaser. A type with no content yet gets a best
  guess (`body`/`field_body`) until the next save or restart.
- **Paragraphs-based and plain-text bodies aren't read yet.** A type whose text lives in
  Paragraphs, or in a plain (unformatted) long-text field, is skipped; a site where no type has
  formatted text falls back to the Umami `article`/`page`/`recipe` mapping. Paragraphs support
  is planned as its own future release (R5 in
  [`specs/backend-and-auth.md`](specs/backend-and-auth.md)).
- **"Pending" undercounts.** `drupal_pending_content` looks at each content type's 50 most
  recently changed items, so older drafts aren't counted (see `docs/drupal-setup.md` §8 for
  why). On sites using Content Moderation, a new draft of content that's already published is
  a separate revision that JSON:API lists don't return, so it isn't counted either. Tracked in
  [`specs/backend-and-auth.md`](specs/backend-and-auth.md) ("Tracked separately").
- **If the site can't be read at start**, Dreachy falls back to the Umami
  `article`/`page`/`recipe` mapping and retries discovery, at most once a minute, as it's used.
- **`drupal_read_article` occasionally acknowledges without reading.** The model says it will
  fetch the article and then ends the turn without calling the tool. Asking a second time works.
- **Site pulse counts are approximate.** Core JSON:API has no collection count, so counts are
  capped at `pulse_sample_limit` (50) items per content type.

## Development

```bash
uv sync
uv run pytest
```

`dreachy/main.py` is the only genuinely new code here: a `ReachyMiniApp` subclass that points
`reachy_mini_conversation_app` at Dreachy's bundled profile/tools (via
`REACHY_MINI_CUSTOM_PROFILE` and friends) and then delegates straight into its `run()` — Dreachy
never forks or reimplements any of the conversation app's own behaviour. It also serves the
settings page (`custom_app_url` + `dreachy/static/index.html`), whose `GET`/`POST /api/config`
routes read/write the instance-path `.env` and a rendered copy of the profile. It also loads
that `.env` and discovers the site's content types before the conversation app builds its tool
specs, and serves `GET /api/schema` for the settings page's content-type checkboxes.

Everything else lives under `dreachy/`:

- `client.py` / `config.py` — a small sync wrapper over Drupal's JSON:API, which discovers the
  site's content model at start.
- `backend.py` — the `Backend` interface the tools, watcher and settings page query through;
  `client.py`'s `JsonApiBackend` implements it.
- `auth.py` — chooses the site login; drupal-api-client (≥0.3.1) handles the tokens.
- `schema.py` — the content-model heuristics (which types, which fields hold text). `client.py`
  fetches; `schema.py` decides.
- `tool_queries.py` — the plain-function query logic behind each tool.
- `tools/` — the five Q&A/watcher `Tool` subclasses the conversation app loads, plus two
  editorial tools (`drupal_pending_content`, `drupal_create_note`) registered only when the
  site login works.
- `common_reactions.py` / `reaction_player.py` — Dreachy's one physical reaction (`perk_up`) and
  the code that plays it on the robot.
- `profile/dreachy/` — the persona: `instructions.txt`, `tools.txt`, `greeting.txt`. This is the
  bundled *default*; at runtime it's copied into the instance path and the settings page's extra
  instructions are appended to that copy, not to these files.
- `static/index.html` — the settings page served at `custom_app_url`.

## Licence

MIT — see [LICENSE](LICENSE).
