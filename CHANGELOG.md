# Changelog

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
