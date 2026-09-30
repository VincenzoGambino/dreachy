# R3 — Editorial tools + gated write-back: Implementation Plan

> **Status: not yet planned.** R3's own tasks (spec R3: conditional
> registration, `drupal_pending_content`, `drupal_create_note`, prompt-injection
> hygiene) are written when R3 planning starts, after R2 is reviewed and merged.
> This file exists now to carry the cleanup that has to happen first.

**Spec:** `specs/backend-and-auth.md` (R3 section, Invariants, Amendments)

---

### Task 1: Cleanup carried from R1 and R2 (do first)

The deferred minor findings from the R1 and R2 whole-branch reviews. The
full wording is in the ledgers:
- `.superpowers/sdd/backend-and-auth/progress.md`, `Final: minor (deferred)` lines;
- `.superpowers/sdd/backend-and-auth-r2/progress.md`, the same.

Same discipline as R1 and R2: every behaviour change starts with a test that
fails first. Items marked *docs* are prose and have no test.

**Needed for R4's seam**

- [ ] **Tools import `DreachySiteError` from the backend, not the JSON:API client** (R2 #11). The four Q&A tools import it from `dreachy.client`: `drupal_whats_new.py:7`, `drupal_read_article.py:7`, `drupal_site_pulse.py:7`, `drupal_find_content.py:7`. Change them to `from dreachy.backend import DreachySiteError`, so the tools no longer depend on the JSON:API module or drupal_api_client under R4's `McpBackend`. Test: a test that imports the tools with `dreachy.client` blocked in `sys.modules` still succeeds.

**Correctness**

- [ ] **Library errors that escape the mapping** (R2 #10). Two errors aren't turned into `DreachySiteError`:
  - a token response without `expires_in` raises `KeyError`;
  - a router answer that isn't JSON raises `JSONDecodeError`.

  They escape `_node_bundles` (`client.py`) and `get_article`'s path branch. Through `_types()`, the `KeyError` can end the watcher's task. Catch `(ValueError, KeyError, TypeError)` in both places through `_site_error`, as `_get_collection` already does. Tests: a token response without `expires_in` makes `refresh_schema()` return False, and doesn't raise; a non-JSON router answer to `get_article` raises `DreachySiteError`.
- [ ] **A path read lets a draft through when the site hides `status`** (R2 #4). `attributes.get("status", True)` fails open. Treat a missing `status` as unpublished whenever `include_unpublished` is False. Test: a draft served without `status` isn't returned by path. Also check, then document, what the published-only `filter[status]=1` does on a site where JSON:API Extras disables or renames `status`. The reviewer thought a 400 plausible but didn't verify it.
- [ ] **A content type deleted or locked mid-session breaks every multi-type query** (R1). `get_recent_nodes` and `find_content` stop at the first type's `DreachySiteError`. Skip that type (and log it) rather than failing the call, but keep failing on auth and site-wide errors. Test: one type answering 404 or 403 mid-session still leaves what's-new working for the others.
- [ ] **A client can be built on a discarded reset** (R1). Right after `reset_client()`, the settings threadpool and the watcher can each build a new client, so a discovery can land on one that's thrown away. Put a lock around `get_client()`'s build. Test: two threads calling `get_client()` after a reset get the same instance.

**Settings page and login**

- [ ] **The page's login status can disagree with what Dreachy does** (R2 #5). `_auth_status` works from raw env values, while `Config.from_env` strips and lowercases them. A hand-edited `DREACHY_AUTH=OAuth`, or a secret of spaces, makes the two disagree. Derive the status from `Config.from_env()` (`uses_oauth`). Test: `DREACHY_AUTH=OAuth` reports `auth: "oauth"` and `active: true`.
- [ ] **A secret field of only spaces erases the saved secret** (R2 #6). Test `payload.client_secret.strip()`, not the raw value. Test: saving `"   "` keeps the saved secret.
- [ ] **"Remove the saved secret" beats a newly typed secret** (R2 #8). A new secret should win, or the page should disable one control when the other is used. Test: saving both keeps the new secret.
- [ ] **The page can't tell a refused login from an unreachable site** (R2 #9). Put the kind of the last discovery error (auth or site) in `/api/schema`. The page then says "the site refused the login" instead of "check the site URL". Test: after a refused login, `/api/schema` reports the auth kind.
- [ ] **A backslash in a secret is corrupted after a restart** (R2 #14). `dotenv.set_key` quoting turns `ab\cd` into a value that reloads differently. Write values so they round-trip, and apply it to every setting written this way. Test: a value containing `\` saves, reloads through `dotenv.load_dotenv`, and compares equal.
- [ ] **"Untick all" is undocumented** (R1). The page doesn't say that unticking every content type means "all types". *Docs:* add it to the hint beside the checkboxes.

**Watcher and logging**

- [ ] **Every save makes the watcher re-baseline and log "site URL changed"** (R1, and R2 #15). It happens even when only the instructions or the login changed. Reword the log line to "settings changed", and keep the baseline when neither the site URL nor the locale changed. Test: an instructions-only save doesn't reset what the watcher counts as already seen.
- [ ] **A stale type selection logs a warning on every schema access** (R1). `select_types` warns on every watcher poll and tool call. Warn once per client. Test: repeated `schema` reads log the warning once.
- [ ] **Dropped clients are never closed** (R2 #13). `reset_client()` orphans them, and they keep the secret and a live token until garbage collection. Close the old client once nothing is using it (for example, deferred until the watcher has switched over). Test: after a reset and the watcher's switch, the old client is closed.

**Tests and docs**

- [ ] **Two tests are weaker than their names** (R2 #12):
  - `test_the_watcher_survives_a_refused_login` only checks the error class hierarchy. Make it run the watcher against a backend that refuses the login, and check it keeps polling.
  - Add a test that router path lookups map auth errors, which a probe shows already works.
- [ ] **Wrong reason for loading `.env` ourselves** (R1). The `_load_instance_env` docstring and `docs/ARCHITECTURE.md` step 1 say upstream loads `.env` only once its stream launches, after it builds the tool specs. The pinned upstream actually loads it at the start of `main.run`. Dreachy loads it itself because it discovers types *before* handing over. *Docs.*
- [ ] **The `ARCHITECTURE.md` troubleshooting row cites the wrong log line** (R1). For "Ignores a content type" it should name `Can't sample node--X, skipping it`, and a type with no formatted text should be logged at all. *Docs, plus one log line (test: the no-text skip is logged).*
