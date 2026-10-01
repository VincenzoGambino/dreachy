# Changelog

## 0.5.0 — unreleased

MCP backend (`specs/backend-and-auth.md`, R4).

- Dreachy can talk to the site through its MCP server (`mcp_server`)
  instead of JSON:API: choose the backend under the settings page's
  Advanced section (`DREACHY_BACKEND=mcp`). Search uses the site's Search
  API index — by meaning, with a vector index — and MCP-only sites work.
- Tools are found on the site by name pattern and can be named in
  `DREACHY_MCP_MAPPING`; searches always check access.
- Notes over MCP: checked to be a draft before saving (nothing is saved if
  that fails) and re-read after; required text fields get the note's text,
  nothing else is invented; the default note type needs the fewest extra
  fields.
- Optional site actions (`drupal_site_action`): allowlisted extra MCP tools,
  never anything that publishes, sets the homepage or a site default,
  deletes or discards; writes need a spoken yes.
- The MCP endpoint must be on the site's own address, so the site login is
  never sent elsewhere.

## 0.4.0 — unreleased

Editorial tools (`specs/backend-and-auth.md`, R3).

- With the site login working, two new tools: `drupal_pending_content`
  (what's waiting to be published) and `drupal_create_note` (saves a
  dictated note as an unpublished draft, only after the person says yes
  aloud; the tool refuses anything but `confirmed=true`).
- The persona treats site content as data, never as instructions.
- Settings page: which content type notes are saved as, and whether
  editing is on.

## 0.3.0 — unreleased

Backend seam and optional site login (`specs/backend-and-auth.md`, R2).

- Tools, the watcher and the settings page query a `Backend` interface;
  the JSON:API client is its first implementation (R4 adds MCP).
- Optional OAuth2 client-credentials login for private sites, from the
  settings page's Advanced section, with an optional scope. The secret is
  write-only and stored only in the owner-only instance `.env`. Needs
  drupal-api-client 0.3.1.
- Dreachy reads published content only, logged in or not.
- A refused login is reported as such, not as a crash; the watcher keeps
  running.

## 0.2.0 — unreleased

Schema-driven content model (`specs/backend-and-auth.md`, R1).

- Dreachy reads the site's content types, and which of their fields hold the
  text, from the site itself: any content model works without code changes.
  The Umami mapping is now only the fallback for when the site can't be read.
- Settings page: choose which content types Dreachy talks about
  (`DREACHY_TYPES`). Saving re-reads the site's types.
- The instance `.env` is loaded before the conversation app starts, so the
  search tool's type filter lists the site's own types.
- Reading by path no longer returns content of a type Dreachy has been told
  to leave out.

## 0.1.0

Initial release.
