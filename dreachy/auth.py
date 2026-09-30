"""Which login Dreachy uses with the Drupal site.

drupal-api-client (>=0.3.1) does the token handling itself: the grant,
caching, the refresh margin and one retry on 401. Dreachy only chooses
the provider from the installer's settings. The secret passes from Config
to the library here and goes nowhere else (spec Invariants).
"""

from __future__ import annotations

from drupal_api_client import OAuthAuth

from .config import Config

# Renew a token with less than this left, so a request never goes out
# carrying one that expires in flight.
TOKEN_REFRESH_MARGIN_SECONDS = 60.0


def library_authentication(config: Config) -> OAuthAuth | None:
    """The drupal-api-client authentication for *config*: None means anonymous."""
    if not config.uses_oauth:
        return None
    return OAuthAuth(
        client_id=config.oauth_client_id,
        client_secret=config.oauth_client_secret,
        # Empty = send none, and the site uses the consumer's default scope
        # (the documented primary path, docs/drupal-setup.md).
        scope=config.oauth_scope or None,
        token_refresh_margin=TOKEN_REFRESH_MARGIN_SECONDS,
    )
