# Changelog

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
