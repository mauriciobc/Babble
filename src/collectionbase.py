# SPDX-License-Identifier: GPL-3.0-or-later
"""Collections: the pieces every collection screen shares.

The membership helpers, the paging and lookup plumbing, the account cache
and the language/date formatting. ``_current_account`` is reached through
this module's attribute by the pages that need the signed-in account, so a
test can patch it in one place.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gio, GLib  # noqa: E402

from . import accountrows, appsettings, feedback, pagenav  # noqa: E402
from .api import accounts as api_accounts  # noqa: E402
from .i18n import gettext as _  # noqa: E402



MAX_MEMBERS = 25

DEFAULT_LIMIT = 10

SEARCH_DELAY_MS = 400

def _items(data):
    """The collection array of a list response (the documentation's
    example spells the key ``collections:``)."""
    if isinstance(data, dict):
        return data.get('collections') or data.get('collections:') or []
    return data or []

def _is_member(item):
    """Whether a Collection item should be shown as a member: everything
    but ``rejected`` and ``revoked`` (``pending`` members are shown with
    a badge). ``state`` is documented as always present; an entry that
    omits it is treated as a member rather than hidden."""
    return item.get('state') not in ('rejected', 'revoked')

def _finished(client, finish, on_ok, on_error):
    def finished(_client, result, _data=None):
        try:
            data = finish(client, result)
        except GLib.Error as error:
            if on_error is not None:
                on_error(error)
            return
        if on_ok is not None:
            on_ok(data)

    return finished

def _send_api(client, start, finish, on_ok=None, on_error=None):
    """Send an `api` function: *start(callback)* queues the request, so
    the path is never spelled here, and *finish* is that module's own
    ``<name>_finish``."""
    return start(_finished(client, finish, on_ok, on_error))

def _current_account(widget=None):
    """The signed-in account dict of the window around *widget* (or the
    active window)."""
    window = pagenav.window_of(widget) if widget is not None else None
    if window is None:
        app = Gio.Application.get_default()
        window = app.get_active_window() if app is not None else None
    return getattr(window, 'account', None) or {}

def collection_limit(account):
    role = (account or {}).get('role') or {}
    try:
        return int(role.get('collection_limit') or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT

_ACCOUNTS = {}

# GET /accounts?id[]= answers at most this many ids: the server raises a
# validation error past DEFAULT_ACCOUNTS_LIMIT (= 40, in
# app/controllers/api/base_controller.rb), though the docs name no limit.
ACCOUNTS_CHUNK = 40

_ERROR_REPORT_SECONDS = 5

_last_error_report = [0]

def _report_lookup_error(widget, error):
    """Toast a failed account lookup, at most once in a few seconds: a
    page of collection rows asks for each row's avatars at once, and one
    outage must not stack a toast per row."""
    now = GLib.get_monotonic_time()
    if now - _last_error_report[0] < _ERROR_REPORT_SECONDS * 1000000:
        return
    _last_error_report[0] = now
    feedback.report_error(widget, _('Could not load accounts'), error)

def fetch_accounts(client, ids, callback, widget=None):
    """``callback({id: account})`` once the accounts for *ids* are known;
    ``GET /accounts?id[]=…`` for the ones not cached, in chunks the
    server accepts. A chunk that fails keeps the others (the callback
    gets what arrived) and is reported on *widget* when one is given."""
    ids = [i for i in dict.fromkeys(ids) if i]
    host = getattr(client, 'instance', '')

    def done():
        callback({i: _ACCOUNTS[(host, i)] for i in ids
                  if (host, i) in _ACCOUNTS})

    missing = [i for i in ids if (host, i) not in _ACCOUNTS]
    if not missing:
        done()
        return

    chunks = [missing[n:n + ACCOUNTS_CHUNK]
              for n in range(0, len(missing), ACCOUNTS_CHUNK)]
    pending = [len(chunks)]
    first_error = []

    def settled():
        pending[0] -= 1
        if pending[0]:
            return
        if first_error and widget is not None:
            _report_lookup_error(widget, first_error[0])
        done()

    def ok(data):
        for account in data or []:
            _ACCOUNTS[(host, account['id'])] = account
        settled()

    def failed(error):
        first_error.append(error)
        settled()

    for chunk in chunks:
        _send_api(client,
                  lambda cb, chunk=chunk: api_accounts.by_ids_async(
                      client, chunk, callback=cb),
                  api_accounts.by_ids_finish,
                  ok, failed)

def remember_account(client, account):
    _ACCOUNTS[(getattr(client, 'instance', ''), account['id'])] = account

def owner_label(account):
    return accountrows.display_name(account) if account else ''

def format_date(iso, fmt='%x'):
    value = GLib.DateTime.new_from_iso8601(iso, None) if iso else None
    return value.to_local().format(fmt) if value else ''

def language_options():
    from .preferences import LANGUAGES
    return LANGUAGES

def default_language_code(collection=None):
    if collection and collection.get('language'):
        return collection['language']
    code = appsettings.settings().get_string('default-language')
    return code or 'en'
