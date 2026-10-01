# R3 live checks on a local DDEV site

These checks replace the multidev for PR #3's "Before merge" list. They run
against a local Drupal under DDEV, prepared by a script, never against the
shared sandbox: the JSON:API write switch is site-wide.

Most checks run **from the laptop**, calling Dreachy's real tools the way the
conversation app does; only the robot and the voice are missing. The ones
about what the robot *says* need the robot, and the robot has to reach the
DDEV site first (see "Robot checks").

## 1. Choose the site

**Recommended: `drupal-headless`**
(`~/Work/Code/stripedcodex/headless/drupal-headless`,
<https://drupal-headless.ddev.site>). It has Drupal 11.4.4 and Umami, with
Content Moderation on `article`, `page` and `recipe`. **Simple OAuth 6.1.1 is
already installed and JSON:API is already read-write**, so the script only adds
Dreachy's pieces. It already has other consumers (`drupal_api_client_test`)
and a scope (`api_write`), so it's probably shared with drupal-api-client's own
tests. The script leaves existing keys, scopes and consumers alone. It adds two
nodes, which matters only if another suite counts nodes.

**Alternative: `reachy/dreachy`**, the original local Umami site. Here the
script also installs Simple OAuth, generates keys and switches JSON:API to
read-write.

Neither site is a git repository, so the script backs up first: a database
snapshot plus `composer.json` and `composer.lock`. It prints the undo commands
when it finishes.

## 2. Prepare it

```bash
cd dreachy-app
scripts/live/ddev-r3-setup.sh ~/Work/Code/stripedcodex/headless/drupal-headless
# or, for reachy/dreachy:  scripts/live/ddev-r3-setup.sh
```

It's idempotent; each run rotates Dreachy's secret. It creates:

- the **`dreachy` role**, with `access content`, `view any unpublished content`,
  `view latest version`, `create article content` and
  `use editorial transition create_new_draft`;
- the **`dreachy` user**, with that role;
- the **`dreachy` scope**: client credentials, **role** granularity pointing at
  the `dreachy` role. In Simple OAuth 6 a scope maps to one permission or one
  role, so a role is how several permissions reach the token;
- the **`dreachy` consumer**: confidential, client credentials, the `dreachy`
  user, and `dreachy` as its default scope;
- **content:** a published article "A note to the robot" (the injection text)
  and an article draft "Draft: park opening hours", authored by admin, which
  is "another editor" from Dreachy's point of view.

It then checks that a token is granted, and prints the `export DREACHY_...`
lines for the next step.

## 3. Laptop checks

Paste the exported variables, then:

```bash
uv run python scripts/live/r3_checks.py
```

Every line should read `PASS`. The script exits non-zero if any check fails.
It creates one unpublished draft, "Dreachy live check HH:MM:SS".

| PR #3 check | Where | How |
|---|---|---|
| §8 setup works: the token, the role, the scope | **Laptop** | The setup script's token smoke test, then L1 |
| "What's pending?" sees another editor's draft | **Laptop** (data) | L3 |
| | **Robot** (wording) | Ask "what's waiting to be published?" |
| Dictate a note, answer no: nothing saved | **Laptop** (the tool's guard) | L4: `confirmed` missing or `"true"` → refused, nothing saved |
| | **Robot** (the conversation) | Dictate a note and say no when it asks "shall I save it as a draft?" |
| Dictate a note, answer yes: unpublished, by `dreachy` | **Laptop** | L5, plus the `ddev drush sql:query` author check the script prints |
| | **Robot** | Dictate, say yes, then find the draft in the admin |
| Injection article read as data | **Laptop** (data path) | L6: read back verbatim, no "Site closed" draft |
| | **Robot** (model behaviour) | Ask it to read "A note to the robot": it reads it, at most remarks on it, and doesn't offer to save |
| Login None: no editorial tools | **Laptop** | L7: editing stays off at start, and both tools refuse |
| Browser pass of the settings page | **Laptop** | Run the app on the robot, or the settings server locally, and open Advanced |
| A note on an unmoderated type | **Not covered here** | Umami moderates all three types. The unit tests cover it (`test_a_note_on_an_unmoderated_type_is_unpublished`, `test_a_status_permission_refusal_says_which_field`) |

## 4. Robot checks

The robot can't resolve `*.ddev.site`: that name points at `127.0.0.1`, which
on the robot is the robot itself. To run the voice checks against this site:

1. Make DDEV's router listen on the LAN:
   `ddev config global --router-bind-all-interfaces=true`, then `ddev poweroff`
   and start the project again.
2. On the robot, over SSH, map the site name to the laptop's LAN address in
   `/etc/hosts`, for example `192.168.1.20 drupal-headless.ddev.site`.
3. On the robot, use `http://` rather than `https://`. The robot doesn't trust
   DDEV's local certificate authority. Dreachy logs its plain-http warning,
   which is expected for this test-only secret on your own network.
4. Install this branch on the robot, since it replaces the robot's Dreachy.
   Enter the URL, the client ID and the secret on the settings page, and
   restart Dreachy.

Steps 1–3 haven't been tried yet. If they don't work, the laptop checks still
cover everything except what the robot says.

## 5. Undo

The setup script prints the restore commands: snapshot restore, composer
files, and the generated keys if any. To remove only the notes the checks
created, delete the drafts titled "Dreachy live check …" in the admin.
