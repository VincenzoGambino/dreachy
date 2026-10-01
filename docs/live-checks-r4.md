# R4 live checks — MCP mode against the DrupalForge sandbox

`scripts/live/r4_checks.py` runs the real tools against the sandbox's MCP
server from the laptop, as the conversation app calls them; only the robot
and the voice are missing. It reads the instance `.env`, then, in its own
process only, sets `DREACHY_BACKEND=mcp`, a blank language prefix, the
`content_vector` search index unless one is set, and a test allowlist of
site actions that includes tools that must be denied.

It saves **one unpublished draft** per run ("Dreachy live check <time>"),
as the default note type, and never publishes. Delete the drafts on the
site afterwards.

```bash
uv run python scripts/live/r4_checks.py
```

## Run of 2026-10-01 (prod-cd9108-f20a4d-wz4f46vp7tgh): 23/23

| Check | Result |
|---|---|
| M1 login, editing and site-action registration | pass; first session 4.5 s |
| M2 discovery | 11 types in 9.1 s; `standard_page` body `description`, required text `preview_text`, required other `ai_automator_status` |
| M3 what's new / watcher poll / pulse | 5.0 s / **1.1 s** / 6.2 s |
| M4 semantic find, "how do I apply" | 7.5 s: How to Apply, Admissions, … — JSON:API title search finds nothing for the same words ("apply" alone: How to Apply) |
| M5 read by title / by path / nothing matching | 5.8 s / 5.4 s (path → Admissions) / "couldn't find anything" |
| M6 notes | unconfirmed refused; configured type `article` refused (no content, so not discovered) with nothing saved; confirmed note saved as a `student_announcement` draft in 8.7 s, exactly one node more |
| M7 pending | lists the new draft |
| M8 site actions | only `canvas_list_targets` offered out of six listed; asks first; runs once confirmed; publish, homepage and `entity_save` never available |

Found and fixed during the run:
- The watcher's poll listed every field (15.3 s). It now asks only for
  status and creation date (one round of two calls, 1.1 s), and reads list
  text fields only for the types of the items they return (what's new
  16.0 s → 5.0 s).
- The site-action spec didn't say which arguments are required
  (`canvas_list_targets` needs `target_type`); it does now.

On the sandbox the OAuth consumer runs as **uid 1 (admin)**, by choice —
both drafts are authored by `admin`, and `system_status` succeeds. Any
account works; for security, a real site shouldn't use user 1
(`docs/drupal-setup.md` §9).

`DREACHY_NOTE_TYPE=article` in the instance `.env` refuses every note on
this sandbox (no article content; on the previous instance its workflow
also had no draft→draft transition): clear it, or list `article` under the
MCP content types once it has a usable workflow.
