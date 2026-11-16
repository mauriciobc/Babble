# SPDX-License-Identifier: GPL-3.0-or-later
"""OAuth with PKCE: the constants, the verifier and the instance name.

The redirect URIs, the scopes and the code challenge are protocol facts,
not transport: they are shared by the authorization flow, the window and
the benchmark, and they change only when the protocol does.
"""

import base64
import hashlib
import secrets as _secrets

try:
    from .i18n import gettext as _  # noqa: E402
except ImportError:  # loaded as a top-level module by the standalone tests
    from i18n import gettext as _  # noqa: E402
# RFC 7636 code_verifier: 43-128 chars from the unreserved set. 32
# random bytes encode to 43 characters, the shortest legal value.
CODE_VERIFIER_BYTES = 32

def generate_code_verifier():
    """A fresh RFC 7636 ``code_verifier`` (43 chars, unreserved set)."""
    return _secrets.token_urlsafe(CODE_VERIFIER_BYTES)

def code_challenge(verifier):
    """The S256 ``code_challenge`` for *verifier*."""
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')

def new_state():
    """A fresh ``state`` value, used to bind the callback to this attempt."""
    return _secrets.token_urlsafe(CODE_VERIFIER_BYTES)

OAUTH_REDIRECT = 'babble://oauth/callback'

OOB_REDIRECT = 'urn:ietf:wg:oauth:2.0:oob'

REDIRECT_URIS = OAUTH_REDIRECT + ' ' + OOB_REDIRECT

# Where a loopback listener answers the redirect. Mastodon accepts a
# plain-http redirect URI and compares it exactly against the registered
# set, so the port the listener got is registered for that one login.
LOOPBACK_CALLBACK_PATH = '/callback'

def loopback_redirect(port):
    """The redirect URI of a listener on 127.0.0.1 *port*."""
    return 'http://127.0.0.1:%d%s' % (port, LOOPBACK_CALLBACK_PATH)

# The one scope set the ordinary login asks for. ``follow`` was the
# third member until Mastodon 3.5 deprecated it: every granular scope it
# granted (read/write of follows, blocks and mutes) is reached through
# ``read``/``write``, so naming it now only puts a deprecated scope on
# the instance's consent screen.
OAUTH_SCOPES = 'read write'

# The moderation scopes, asked for only by the admin dashboard and only
# once the account's role says it could use them. A registration that
# carries them is harmless -- Mastodon grants nothing until an
# authorization names a scope -- but an authorization that names them
# puts "All administrative functions" on the consent screen, so every
# ordinary login must stay on OAUTH_SCOPES.
ADMIN_SCOPES = OAUTH_SCOPES + ' admin:read admin:write'

# Shown as the application's own homepage on the instance's consent
# screen. It is Babble's address, never the server the user is signing
# in to.
APP_WEBSITE = 'https://github.com/mauriciobc/Babble'

# Keyring kind of the OAuth client credentials. They are keyed per
# account as well as per instance: every authorization registers its own
# application, so two accounts on one instance hold different credential
# pairs and sharing one entry would make the older token unrevokable.
CLIENT_CREDENTIALS_KIND = 'oauth-client:'

def normalize_instance(url):
    """The canonical https origin for *url*.

    A plaintext ``http://`` instance is refused outright: the bearer
    token and the client secret would cross the network in the clear.
    """
    url = url.strip()
    if '://' not in url:
        url = 'https://' + url
    url = url.rstrip('/')
    scheme, sep, rest = url.partition('://')
    if not sep:
        raise ValueError(_('Invalid server address: %s') % url)
    if scheme.lower() != 'https':
        raise ValueError(_('Babble only connects over HTTPS: %s') % url)
    return url
