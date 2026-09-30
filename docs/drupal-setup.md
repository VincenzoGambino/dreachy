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

## 6. Check it works

To see the login make a difference, make the site private:

1. At **`/admin/people/permissions`**, remove **View published content** from
   **Anonymous user**.
2. With Dreachy's login set to **None**, ask "what's new?": the robot says it
   can't reach the site.
3. Switch Dreachy's login to **OAuth client credentials** and save: the same
   question gets a normal answer.
4. In the web server's access log, Dreachy's requests to `/jsonapi/…` carry
   `Authorization: Bearer …`. The token itself comes from `POST /oauth/token`.

Restore the anonymous permission afterwards if the site should be public.

## 7. Revoking

To cut Dreachy off, delete the consumer or give it a new secret. From then on,
Dreachy tells people it can't get into the site, and keeps watching for the
site to come back. To reconnect after rotating the secret, paste the new one
into Dreachy's settings page.
