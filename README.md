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
  - **Anonymous read access** to whichever content types you expose (Dreachy doesn't
    authenticate — this is a read-only, publicly-reachable demo companion, not an admin tool).
  - At least one content type among `article`, `page`, `recipe` with a `field_body` (article/page)
    or `field_recipe_instruction` + `field_summary` (recipe). Any Drupal site built on the
    **Umami demo profile** matches this out of the box; other content models will need either a
    matching field layout or a small edit to `dreachy/client.py`'s `_TEXT_FIELDS` mapping.
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
- **Extra instructions** (optional) — free text appended to Dreachy's built-in persona (tone,
  language, anything else). Built-in guardrails (e.g. "only answer from site content") stay in
  force either way, since this is appended, not a replacement. Takes effect the next time the
  app starts.

No SSH needed for either of those. `HF_TOKEN` (needed for the realtime voice/LLM backend) is
handled separately by the robot's own Hugging Face sign-in in the dashboard — not something
Dreachy's own settings page touches.

If you'd rather configure it by hand (e.g. scripting a fresh install), both settings are backed
by a `.env` file at `~/.local/share/dreachy/.env` on the robot — see
[`.env.example`](.env.example).

## Known issues

- **The site must be reachable from the robot.** Dreachy runs on the Reachy Mini, not on your
  laptop, so a local development URL (`localhost`, `*.ddev.site`, a private hostname) won't
  resolve there. Use a publicly reachable site, a tunnel, or a hosting sandbox.
- **The content model is Umami's.** `article`/`page`/`recipe` and their text fields are what
  Dreachy knows about, set in [`dreachy/config.py`](dreachy/config.py) and `_TEXT_FIELDS` in
  [`dreachy/client.py`](dreachy/client.py). Other content models need those two edits. Moving
  this configuration into the Drupal site itself is the main thing planned next.
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
routes read/write the instance-path `.env` and a rendered copy of the profile.

Everything else lives under `dreachy/`:

- `client.py` / `config.py` — a small sync wrapper over Drupal's JSON:API.
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
