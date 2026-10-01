# Setting up a Drupal site for Dreachy's login

Dreachy reads a Drupal site over JSON:API. For a public site that's all it
needs: anonymous access, nothing to set up here. This page is for a site whose
content anonymous visitors **can't** see, such as a private site or an
intranet. There, Dreachy logs in with OAuth2 client credentials, as its own
Drupal user.

Written against **Simple OAuth 6.1.1** (22 May 2026, Drupal `^10.3 || ^11`).
Sources:

- the project page, <https://www.drupal.org/project/simple_oauth>;
- the module README on the 6.1.x branch,
  <https://git.drupalcode.org/project/simple_oauth/-/blob/6.1.x/README.md>;
- issue [#3495325](https://www.drupal.org/project/simple_oauth/issues/3495325),
  which limits client-credentials tokens to the consumer's own scopes.

Admin paths and labels can move between releases. If a screen differs, the
steps' intent still holds.

## 1. When you need this

Only when the content Dreachy should talk about isn't public. With anonymous
access, leave Dreachy's settings page on **Login: None** and skip this page.

The site must be served over **https**. Dreachy sends its client secret to
the site's `/oauth/token` endpoint, and over plain http anyone on the network
path could read it. Dreachy logs a warning if you try.

Whether it's logged in or not, Dreachy only ever reads **published** content.
It never reads drafts aloud, searches them or reacts to them.

## 2. Install Simple OAuth and generate keys

1. Install and enable the module (it brings in `consumers`):

   ```bash
   composer require 'drupal/simple_oauth:^6.1'
   drush en simple_oauth
   ```

2. Generate a key pair **outside the web root**, for example in a `keys/`
   directory beside it:

   ```bash
   openssl genrsa -out private.key 2048
   openssl rsa -in private.key -pubout > public.key
   ```

   Make both files readable by the web server's user only.
3. At **`/admin/config/people/simple_oauth`**, set the public and private key
   paths to those files and save.

## 3. A dedicated `dreachy` role and user

1. Create a role `dreachy` with a single permission: **View published
   content** (`access content`). Dreachy only reads, so it needs nothing else.
2. Create a user `dreachy` with that role. This is the account the site sees
   when Dreachy logs in. Give it a long random password; nobody logs in as it
   interactively.

## 4. A scope that grants only what Dreachy reads

For the client-credentials grant, Simple OAuth 6 takes a token's permissions
from its **scope**, not from the user's roles. So the scope is where least
privilege is enforced.

1. Go to **`/admin/config/people/simple_oauth/oauth2_scope/dynamic/add`**.
2. Name it `dreachy`, enable it for the **Client Credentials** grant, set the
   granularity to **Permission**, and choose **View published content**
   (`access content`).

Enable no broader scope for the client-credentials grant.

## 5. The OAuth client (consumer)

1. Go to **`/admin/config/services/consumer/add`**.
2. Give it a label (`Dreachy`) and a client ID (`dreachy`), and set
   **Is Confidential?** on, with a long random **secret**.
3. Under grant types, enable **Client Credentials**. Under the Client
   Credentials settings, choose the **`dreachy` user**.
4. Set the consumer's **scopes** to `dreachy`. This is required: by default
   Dreachy doesn't name a scope when it asks for a token, and with no default
   scope on the consumer the site refuses the request.

   *Alternative:* Dreachy's settings page has an optional **Scope** field
   (`DREACHY_OAUTH_SCOPE`). Filled in, Dreachy asks for that scope by name.
   The consumer must still allow it, so the default-scope setup above
   remains the recommended path; the field is for sites that give one
   consumer several scopes.
5. Save.
6. On Dreachy's settings page, open **Advanced: site login**:
   - choose **OAuth client credentials**;
   - enter the client ID;
   - leave **Scope** blank (the consumer's default applies);
   - paste the secret, then save.

   That page is the only place the secret goes. Don't put it in a file in the
   repo, in the profile or in a chat. Once saved, Dreachy stores it in its own
   instance `.env`, readable by its owner only, and never shows it again.

   A login belongs to one site. If you later change Dreachy's site URL,
   Dreachy removes the saved login rather than send it to the new address;
   enter the new site's credentials again.

## 6. Check it works

To see the login make a difference, make the site private:

1. At **`/admin/people/permissions`**, remove **View published content** from
   **Anonymous user**.
2. With Dreachy's login set to **None**, ask "what's new?": the robot finds
   nothing. Drupal still answers anonymous JSON:API requests, but with every
   node filtered out, so to Dreachy the site looks empty rather than
   unreachable.
3. Switch Dreachy's login to **OAuth client credentials** and save: the same
   question gets a normal answer.
4. In the web server's access log, Dreachy's requests to `/jsonapi/…` carry
   `Authorization: Bearer …`. The token itself comes from `POST /oauth/token`.

Restore the anonymous permission afterwards if the site should be public.

## 7. Revoking

To cut Dreachy off, delete the consumer or give it a new secret. Once the
token Dreachy already holds stops working (it may stay valid until it expires,
typically within minutes), Dreachy tells people it can't get into the site,
and keeps watching for it to come back. To reconnect after rotating the
secret, paste the new one into Dreachy's settings page.

To remove Dreachy's copy of the secret too, tick **Remove the saved secret**
on its settings page and save.

## 8. Letting Dreachy save drafts (optional)

With the login working, Dreachy can also say what's waiting to be published
and save a note someone dictates as an **unpublished draft**. It never
publishes, updates or deletes anything. It turns this on at start only when
the login works; restart Dreachy after setting it up.

1. **Allow writes over JSON:API.** At **`/admin/config/services/jsonapi`**,
   choose **Accept all JSON:API create, read, update, and delete
   operations**. Core accepts only reads by default. This opens writes to
   every API client, each limited by its own permissions, so review who else
   uses the API first.
2. **Grant the `dreachy` role, and add to the `dreachy` scope, the
   permissions below.** For client credentials, the scope is what counts
   (§4).
   - **View any unpublished content** (`view any unpublished content`), so
     "what's pending?" can see everyone's drafts. This comes with the Content
     Moderation module. Without Content Moderation, grant **View own
     unpublished content** (`view own unpublished content`) instead: Dreachy
     then sees only the drafts it saved itself.

     > **Never grant `bypass node access` to make up for it.** That
     > permission lets its holder view, edit and delete *every* node,
     > published or not, whatever the other settings say. Anyone holding
     > Dreachy's client secret would hold that power too.
   - **View the latest version** (`view latest version`), on sites using
     Content Moderation.
   - **Create new content** for the type notes are saved as
     (`create {type} content`).
   - On a moderated type: the transition that creates a draft, for example
     **Editorial workflow: Use Create New Draft transition**
     (`use editorial transition create_new_draft`).
   - Only if the note type is **not** moderated: **Administer node published
     status** (`administer node published status`). Drupal lets only this
     permission set whether a new node is published, and Dreachy must set it
     to unpublished. It's marked as a restricted permission: with it, Dreachy
     could in principle publish what it creates, though the code never does,
     and it grants no access to anyone else's content. A moderated note type
     avoids it altogether, so prefer one.
3. **Choose the note type** in Dreachy's settings page (Advanced: **Save
   dictated notes as**). The default is the first content type Dreachy talks
   about. Notes are saved as plain text (core's `plain_text` format), which
   every role may use.
4. Notes are authored by the `dreachy` user.

**How "pending" is counted:** Dreachy reads each content type's 50 most
recently changed items and counts the unpublished ones that aren't archived.
It can't simply ask the site for "all drafts": Drupal's JSON:API narrows any
filtered listing to published content (plus the account's own) unless the
account may bypass node access, which Dreachy must never have. Drafts older
than a type's last 50 changes aren't counted.

**Not covered:** a new draft of content that's already published (Content
Moderation's "Create New Draft" from Published) isn't counted as pending.
