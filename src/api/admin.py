# SPDX-License-Identifier: GPL-3.0-or-later
"""Admin (moderation) endpoints (audit rows AD-1..AD-12), declared once and
sent through `api.core`.

Every function is a `<name>_async(client, ..., callback=None,
cancellable=None)` / `<name>_finish(client, result)` pair, and the request on
the wire is the one the admin dashboard sent before: same verb, path, form,
JSON and params. All of them need the ``admin:read`` or ``admin:write``
scopes, which the login does not ask for (the dashboard runs its own extra
authorization), and a role with the matching permission.

Three table picks, each over a table of declarations: `account_review_async`
over ``ACTIONS`` (the verbs that review or undo a moderation state),
`report_transition_async` over ``REPORT_ACTIONS`` (assign, resolve...) and
`rule_create_async` / `rule_delete_async` over ``RULES`` (the four rule
lists: a federation block, a federation allow, a blocked e-mail domain and
an IP rule). A paged view takes its path from ``list_path(kind)`` over
``LISTS``.

Carried over from the audit, not fixed here (phase 5): the JSON bodies of
``PUT /admin/reports/:id``, ``POST /admin/measures`` and ``POST
/admin/dimensions`` are documented as form data (AD-2, AD-12);
``DELETE /admin/ip_blocks/:id`` is documented with the scope
``admin:write:domain_blocks``, which looks like a docs error, so the scope
declared here is ``admin:write:ip_blocks``.

`min_version` is None throughout: the app gates the dashboard on the role
and the scope probe, never on the API version, so the release is in
``mastodon_version`` and nothing here changes what the app asks of an
instance.
"""

from . import core

DOCS = 'https://docs.joinmastodon.org/methods/admin/'


def _declare(name, method, path, scope, docs, anchor, note=None,
             version=None):
    return core.declare('admin.' + name, method, path, scope, None,
                        DOCS + docs + '/' + anchor, note=note,
                        mastodon_version=version)


_JSON = 'JSON body, though the docs show form data (audit %s)'

# Accounts

ACCOUNTS = _declare('accounts.index', 'GET', '/api/v2/admin/accounts',
                    'admin:read:accounts', 'accounts', '#v2',
                    note='filters: origin, status, username, display_name, '
                         'email, ip', version='3.5.0')
ACCOUNTS_PROBE = _declare(
    'accounts.probe', 'GET', '/admin/accounts', 'admin:read:accounts',
    'accounts', '#v1',
    note='sent with limit=1 as the cheapest admin call: 200 when the token '
         'has the scope and the role, 403 when not; the v1 list is not '
         'deprecated per the docs. The v2 list needs the same scope and '
         'role (3.5.0), so it could be the probe with the same 200/403 '
         'answer; v1 is kept because it is older and the dashboard works '
         'on servers that predate v2 (audit item 6, phase 5)',
    version='2.9.1')
ACCOUNT_ACTION = _declare(
    'accounts.action', 'POST', '/admin/accounts/%s/action',
    'admin:write:accounts', 'accounts', '#action',
    note='form type, report_id, text, send_email_notification',
    version='2.9.1')
APPROVE = _declare('accounts.approve', 'POST', '/admin/accounts/%s/approve',
                   'admin:write:accounts', 'accounts', '#approve',
                   version='2.9.1')
REJECT = _declare('accounts.reject', 'POST', '/admin/accounts/%s/reject',
                  'admin:write:accounts', 'accounts', '#reject',
                  version='2.9.1')
ENABLE = _declare('accounts.enable', 'POST', '/admin/accounts/%s/enable',
                  'admin:write:accounts', 'accounts', '#enable',
                  version='2.9.1')
UNSILENCE = _declare('accounts.unsilence', 'POST',
                     '/admin/accounts/%s/unsilence', 'admin:write:accounts',
                     'accounts', '#unsilence', version='2.9.1')
UNSUSPEND = _declare('accounts.unsuspend', 'POST',
                     '/admin/accounts/%s/unsuspend', 'admin:write:accounts',
                     'accounts', '#unsuspend', version='2.9.1')
UNSENSITIVE = _declare('accounts.unsensitive', 'POST',
                       '/admin/accounts/%s/unsensitive',
                       'admin:write:accounts', 'accounts', '#unsensitive',
                       version='3.3.0')

# Reports

REPORTS = _declare('reports.index', 'GET', '/admin/reports',
                   'admin:read:reports', 'reports', '#get',
                   note='filter: resolved', version='2.9.1')
REPORT_UPDATE = _declare('reports.update', 'PUT', '/admin/reports/%s',
                         'admin:write:reports', 'reports', '#update',
                         note=_JSON % 'AD-2', version='3.5.0')
ASSIGN_TO_SELF = _declare('reports.assign_to_self', 'POST',
                          '/admin/reports/%s/assign_to_self',
                          'admin:write:reports', 'reports',
                          '#assign_to_self', version='2.9.1')
UNASSIGN = _declare('reports.unassign', 'POST',
                    '/admin/reports/%s/unassign', 'admin:write:reports',
                    'reports', '#unassign', version='2.9.1')
RESOLVE = _declare('reports.resolve', 'POST', '/admin/reports/%s/resolve',
                   'admin:write:reports', 'reports', '#resolve',
                   version='2.9.1')
REOPEN = _declare('reports.reopen', 'POST', '/admin/reports/%s/reopen',
                  'admin:write:reports', 'reports', '#reopen',
                  version='2.9.1')

# The dashboard

MEASURES = _declare('measures', 'POST', '/admin/measures', 'admin:read',
                    'measures', '#get', note=_JSON % 'AD-12',
                    version='3.5.0')
DIMENSIONS = _declare('dimensions', 'POST', '/admin/dimensions',
                      'admin:read', 'dimensions', '#get',
                      note=_JSON % 'AD-12', version='3.5.0')

# The four rule lists

DOMAIN_BLOCKS = _declare('domain_blocks.index', 'GET',
                         '/admin/domain_blocks', 'admin:read:domain_blocks',
                         'domain_blocks', '#get', version='4.0.0')
DOMAIN_BLOCK_CREATE = _declare(
    'domain_blocks.create', 'POST', '/admin/domain_blocks',
    'admin:write:domain_blocks', 'domain_blocks', '#create',
    note='form domain, severity, reject_media, reject_reports, obfuscate, '
         'private_comment, public_comment', version='4.0.0')
DOMAIN_BLOCK_DELETE = _declare(
    'domain_blocks.delete', 'DELETE', '/admin/domain_blocks/%s',
    'admin:write:domain_blocks', 'domain_blocks', '#delete',
    note='the docs describe :id as a DomainAllow, a copy-paste slip; it is '
         'the DomainBlock id from the list (audit item 7)', version='4.0.0')
DOMAIN_ALLOWS = _declare('domain_allows.index', 'GET',
                         '/admin/domain_allows', 'admin:read:domain_allows',
                         'domain_allows', '#get', version='4.0.0')
DOMAIN_ALLOW_CREATE = _declare(
    'domain_allows.create', 'POST', '/admin/domain_allows',
    'admin:write:domain_allows', 'domain_allows', '#create',
    note='form domain', version='4.0.0')
DOMAIN_ALLOW_DELETE = _declare(
    'domain_allows.delete', 'DELETE', '/admin/domain_allows/%s',
    'admin:write:domain_allows', 'domain_allows', '#delete',
    note='the :id is the DomainAllow id from the list (audit item 7)',
    version='4.0.0')
EMAIL_BLOCKS = _declare(
    'email_domain_blocks.index', 'GET', '/admin/email_domain_blocks',
    'admin:read:email_domain_blocks', 'email_domain_blocks', '#get',
    version='4.0.0')
EMAIL_BLOCK_CREATE = _declare(
    'email_domain_blocks.create', 'POST', '/admin/email_domain_blocks',
    'admin:write:email_domain_blocks', 'email_domain_blocks', '#create',
    note='form domain', version='4.0.0')
EMAIL_BLOCK_DELETE = _declare(
    'email_domain_blocks.delete', 'DELETE',
    '/admin/email_domain_blocks/%s', 'admin:write:email_domain_blocks',
    'email_domain_blocks', '#delete', version='4.0.0')
IP_BLOCKS = _declare('ip_blocks.index', 'GET', '/admin/ip_blocks',
                     'admin:read:ip_blocks', 'ip_blocks', '#get',
                     version='4.0.0')
IP_BLOCK_CREATE = _declare(
    'ip_blocks.create', 'POST', '/admin/ip_blocks',
    'admin:write:ip_blocks', 'ip_blocks', '#create',
    note='form ip, severity, comment, expires_in', version='4.0.0')
IP_BLOCK_DELETE = _declare(
    'ip_blocks.delete', 'DELETE', '/admin/ip_blocks/%s',
    'admin:write:ip_blocks', 'ip_blocks', '#delete',
    note='the docs name the scope admin:write:domain_blocks here, probably '
         'a docs error (audit AD-11); Babble requests the matching '
         'admin:write:ip_blocks', version='4.0.0')

# Verbs on an account that review it or undo a moderation state.
ACTIONS = {
    'approve': APPROVE, 'reject': REJECT, 'enable': ENABLE,
    'unsilence': UNSILENCE, 'unsuspend': UNSUSPEND,
    'unsensitive': UNSENSITIVE,
}
# Verbs on a report.
REPORT_ACTIONS = {
    'assign_to_self': ASSIGN_TO_SELF, 'unassign': UNASSIGN,
    'resolve': RESOLVE, 'reopen': REOPEN,
}
# The rule lists: kind -> (create, delete).
RULES = {
    'domain_blocks': (DOMAIN_BLOCK_CREATE, DOMAIN_BLOCK_DELETE),
    'domain_allows': (DOMAIN_ALLOW_CREATE, DOMAIN_ALLOW_DELETE),
    'email_domain_blocks': (EMAIL_BLOCK_CREATE, EMAIL_BLOCK_DELETE),
    'ip_blocks': (IP_BLOCK_CREATE, IP_BLOCK_DELETE),
}
# Reads a paged view takes by path.
LISTS = {
    'accounts': ACCOUNTS, 'reports': REPORTS,
    'domain_blocks': DOMAIN_BLOCKS, 'domain_allows': DOMAIN_ALLOWS,
    'email_domain_blocks': EMAIL_BLOCKS, 'ip_blocks': IP_BLOCKS,
}


def list_path(kind, *ids):
    """The path of a paged read (``accounts``, ``reports`` or a rule list)
    for the views that take a path rather than send a call; none has a
    placeholder, so *ids* is empty."""
    return LISTS[kind].path % ids


def _finish(client, result):
    return core.finish(client, result)


# Accounts

def accounts_probe_async(client, callback=None, cancellable=None):
    """The scope probe: one account, ``limit=1``."""
    return core.send(client, ACCOUNTS_PROBE, params={'limit': 1},
                     callback=callback, cancellable=cancellable)


accounts_probe_finish = _finish


def account_action_async(client, account_id, form, callback=None,
                         cancellable=None):
    """Take a moderation action; *form* is built by the take-action dialog
    (type, text, send_email_notification, and report_id when it comes from
    a report)."""
    return core.send(client, ACCOUNT_ACTION, account_id, form=form,
                     callback=callback, cancellable=cancellable)


account_action_finish = _finish


def account_review_async(client, account_id, operation, callback=None,
                         cancellable=None):
    """Send *operation* (a key of ``ACTIONS``) on an account; an unknown
    one raises KeyError."""
    return core.send(client, ACTIONS[operation], account_id,
                     callback=callback, cancellable=cancellable)


account_review_finish = _finish


# Reports

def report_update_async(client, report_id, body, callback=None,
                        cancellable=None):
    """Change a report's category and rules; *body* is sent as JSON."""
    return core.send(client, REPORT_UPDATE, report_id, json_body=body,
                     callback=callback, cancellable=cancellable)


report_update_finish = _finish


def report_transition_async(client, report_id, operation, callback=None,
                            cancellable=None):
    """Send *operation* (a key of ``REPORT_ACTIONS``) on a report; an
    unknown one raises KeyError."""
    return core.send(client, REPORT_ACTIONS[operation], report_id,
                     callback=callback, cancellable=cancellable)


report_transition_finish = _finish


# The dashboard

def measures_async(client, body, callback=None, cancellable=None):
    """*body* is the JSON ``{start_at, end_at, keys}``."""
    return core.send(client, MEASURES, json_body=body, callback=callback,
                     cancellable=cancellable)


measures_finish = _finish


def dimensions_async(client, body, callback=None, cancellable=None):
    """*body* is the JSON ``{start_at, end_at, limit, keys}``."""
    return core.send(client, DIMENSIONS, json_body=body, callback=callback,
                     cancellable=cancellable)


dimensions_finish = _finish


# Rules

def rule_create_async(client, kind, form, callback=None, cancellable=None):
    """Add an entry to the rule list *kind* (a key of ``RULES``); an
    unknown one raises KeyError."""
    return core.send(client, RULES[kind][0], form=form, callback=callback,
                     cancellable=cancellable)


rule_create_finish = _finish


def rule_delete_async(client, kind, item_id, callback=None,
                      cancellable=None):
    """Remove entry *item_id* from the rule list *kind*."""
    return core.send(client, RULES[kind][1], item_id, callback=callback,
                     cancellable=cancellable)


rule_delete_finish = _finish
