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
this package only supplies its own persona (a bundled profile) and five tools that talk to a
Drupal site over JSON:API.

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
- `tools/` — the five `Tool` subclasses the conversation app loads.
- `common_reactions.py` / `reaction_player.py` — Dreachy's one physical reaction (`perk_up`) and
  the code that plays it on the robot.
- `profile/dreachy/` — the persona: `instructions.txt`, `tools.txt`, `greeting.txt`. This is the
  bundled *default*; at runtime it's copied into the instance path and the settings page's extra
  instructions are appended to that copy, not to these files.
- `static/index.html` — the settings page served at `custom_app_url`.

## Licence

MIT — see [LICENSE](LICENSE).
