# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop notifications for new Mastodon notifications.

Notifications arrive from the streaming socket; while that is down the
notifier polls ``GET /api/v1/notifications`` itself, every
:data:`POLL_SECONDS` (twice that in power saver mode), never while the
system is offline, and immediately once it is back. Nothing is shown
while the window is focused (the Notifications page already is the
place for them), only the types ticked in ``desktop-notification-types``
are announced, and with ``group-desktop-notifications`` notifications of
one type about one post replace each other instead of piling up.
"""

import html
import re

import gi

gi.require_version('Gtk', '4.0')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import GLib, Gio, GObject  # noqa: E402

from . import sysstate  # noqa: E402
from .accounts import plain_name, strip_shortcodes  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .notificationcopy import (DESKTOP_TITLE_DEFAULT,  # noqa: E402
                               DESKTOP_TITLES)
from .statustext import parse_content  # noqa: E402

POLL_SECONDS = 60
BODY_LIMIT = 200

TITLES = DESKTOP_TITLES


def html_to_text(markup):
    """Plain text of a status ``content`` fragment.

    Over :func:`statustext.parse_content`, which already turns this HTML
    into text segments. This had its own HTMLParser for the same job,
    and the two disagreed on exactly the case that matters in a
    notification: a content warning. This one kept the *body* as well as
    the warning text, so a notify about a hidden post could leak a line
    of it -- ``notification_body`` prefers ``spoiler_text``, but only
    after this had already read the body it was trying to hide.
    """
    try:
        segments = parse_content(markup)
    except Exception:  # a malformed body must not silence the notify
        return html.unescape(re.sub(r'<[^>]+>', ' ', markup or ''))
    return re.sub(r'[ \t]*\n[ \t\n]*', '\n',
                  ''.join(s['text'] for s in segments)).strip()


def notification_body(notification):
    """The post a notification is about as short plain text; a content
    warning stands in for the text it hides."""
    status = notification.get('status') or {}
    text = strip_shortcodes(status.get('spoiler_text') or '') \
        or html_to_text(status.get('content'))
    if len(text) > BODY_LIMIT:
        text = text[:BODY_LIMIT - 1].rstrip() + '…'
    return text


def actor_list(names):
    """'A', 'A and B', 'A, B and 3 others'."""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return _('%(first)s and %(second)s') % {'first': names[0],
                                                'second': names[1]}
    return ngettext('%(first)s, %(second)s and %(count)d other',
                    '%(first)s, %(second)s and %(count)d others',
                    len(names) - 2) % {'first': names[0],
                                       'second': names[1],
                                       'count': len(names) - 2}


def notification_title(kind, names):
    """Title for a notification about *names*; the plural form is chosen
    with ngettext so languages with more than two forms work."""
    one, many = TITLES.get(kind, DESKTOP_TITLE_DEFAULT)
    template = ngettext(one, many, len(names)) if one != many else one
    return template % actor_list(names)


class DesktopNotifier(GObject.Object):
    """Turns notification JSON into Gio.Notification objects.

    ``window`` supplies ``client``, ``settings``, ``is_active()`` and
    ``get_visible()``; the notifier looks them up lazily so one instance
    survives account switches (``reset()`` forgets the old account)."""

    def __init__(self, window, application=None):
        super().__init__()
        self.window = window
        self._application = application
        self._groups = {}
        self._seen = set()
        self._last_id = None
        self._poll_source = 0
        self._polling_client = None
        self._system = sysstate.monitor()
        self._online = self._system.props.online
        connect_weak(self._system, 'notify::online', self._on_online_changed)
        connect_weak(self._system, 'notify::power-saver',
                     self._on_power_saver_changed)

    # Application access

    def application(self):
        return self._application or Gio.Application.get_default()

    def reset(self):
        self.stop_polling()
        self._groups.clear()
        self._seen.clear()
        self._last_id = None

    # Policy

    def window_inactive(self):
        win = self.window
        return not win.get_visible() or not win.is_active()

    def wants(self, notification):
        settings = self.window.settings
        return notification.get('type') in \
            settings.get_strv('desktop-notification-types')

    # Dispatch

    def handle(self, notification, force=False):
        """Announce *notification* if the policy allows. Returns the
        ``Gio.Notification`` sent, or None (used by the tests and the
        harness)."""
        nid = str(notification.get('id') or '')
        if nid and nid in self._seen:
            return None
        if nid:
            self._seen.add(nid)
            if len(self._seen) > 500:
                self._seen = set(sorted(self._seen)[-250:])
            if self._last_id is None or _id_key(nid) > _id_key(self._last_id):
                self._last_id = nid
        if not force and not self.window_inactive():
            return None
        if not self.wants(notification):
            return None
        app = self.application()
        if app is None:
            return None

        kind = notification.get('type') or ''
        status = notification.get('status') or {}
        name = plain_name(notification.get('account') or {})
        group = self.window.settings.get_boolean('group-desktop-notifications')
        if group:
            key = '%s:%s' % (kind, status.get('id') or '')
            names = self._groups.setdefault(key, [])
            if name not in names:
                names.append(name)
            platform_id = 'babble-%s' % key
        else:
            names = [name]
            platform_id = 'babble-%s' % (nid or kind)
        note = Gio.Notification.new(notification_title(kind, names))
        body = notification_body(notification)
        if body:
            note.set_body(body)
        note.set_priority(Gio.NotificationPriority.HIGH
                          if kind == 'mention'
                          else Gio.NotificationPriority.NORMAL)
        note.set_default_action_and_target(
            'app.open-notifications', GLib.Variant('s', nid))
        icon = self._avatar_icon(notification.get('account') or {})
        if icon is not None:
            note.set_icon(icon)
        if len(names) == 1:
            self._add_buttons(note, kind, notification, status)
        app.send_notification(platform_id, note)
        return note

    def _add_buttons(self, note, kind, notification, status):
        account = getattr(self.window, 'account', None)
        if account is None:
            return
        key = account_key(account)
        if kind == 'follow':
            actor = notification.get('account') or {}
            relationship = notification.get('relationship') or {}
            following = relationship.get('following',
                                         actor.get('following'))
            if actor.get('id') and following is not True:
                note.add_button_with_target(
                    _('Follow Back'), 'app.follow-back',
                    GLib.Variant('(ss)', (key, str(actor['id']))))
            return
        if not status.get('id') or kind not in _STATUS_BUTTON_KINDS:
            return
        target = GLib.Variant('(ss)', (key, str(status['id'])))
        # The shell shows at most three buttons: Reply, Favourite, Boost.
        if kind == 'mention':
            _remember_status(key, status)
            note.add_button_with_target(_('Reply'), 'app.reply', target)
        note.add_button_with_target(
            _('Favourite'), 'app.favourite', target)
        if status.get('visibility') not in ('direct', 'private'):
            note.add_button_with_target(
                _('Boost'), 'app.boost', target)

    @staticmethod
    def _avatar_icon(account):
        """The avatar a notification row already decoded, if any.

        Through the store's own getter, not a private dict: reading
        another module's internals is what made this break silently when
        the avatar cache moved to imagecache.
        """
        from .mediacache import AvatarCache
        url = account.get('avatar') or account.get('avatar_static') or ''
        return AvatarCache.store.get(url) if url else None

    # Polling (stream fallback)

    def start_polling(self):
        """Poll for notifications while the stream is down."""
        client = self.window.client
        if client is None or not client.token:
            return
        if self._poll_source and self._polling_client is client:
            return
        self.stop_polling()
        self._polling_client = client
        if self._last_id is None and self._online:
            self._poll(initial=True)
        self._arm_poll_timer()

    def poll_interval(self):
        """Seconds between polls; power saver mode halves the rate."""
        if self._system.props.power_saver:
            return POLL_SECONDS * 2
        return POLL_SECONDS

    def _arm_poll_timer(self):
        if self._poll_source:
            GLib.source_remove(self._poll_source)
        self._poll_source = GLib.timeout_add_seconds(
            self.poll_interval(), self._on_poll_tick)

    def stop_polling(self):
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0
        self._polling_client = None

    def _on_poll_tick(self):
        if self.window.client is not self._polling_client:
            self._poll_source = 0
            return GLib.SOURCE_REMOVE
        if not self._online or self._polling_client.rate_limited():
            # Offline nothing gets through; rate limited, the instance
            # asked us to back off. Keep the timer but do not spend a
            # request until that is over.
            return GLib.SOURCE_CONTINUE
        # Without a cursor (the first poll skipped offline, or failed)
        # only the newest one is fetched, as a starting point, not news.
        self._poll(initial=self._last_id is None)
        return GLib.SOURCE_CONTINUE

    def _on_online_changed(self, system, _pspec):
        online = system.props.online
        if online == self._online:
            return
        self._online = online
        if not online or not self._poll_source \
                or self.window.client is not self._polling_client \
                or self._polling_client.rate_limited():
            return
        # Catch up now, not up to an interval late, and count the next
        # poll from this one.
        self._poll(initial=self._last_id is None)
        self._arm_poll_timer()

    def _on_power_saver_changed(self, *_args):
        if self._poll_source:
            self._arm_poll_timer()

    def _poll(self, initial=False):
        client = self._polling_client
        params = {'limit': 1 if initial else 20}
        if not initial and self._last_id is not None:
            params['since_id'] = self._last_id
        client.request_async(
            'GET', '/notifications', params=params,
            callback=lambda c, result: self._on_polled(c, result, initial))

    def _on_polled(self, client, result, initial):
        try:
            notifications = client.request_finish(result)
        except GLib.Error:
            return
        if client is not self._polling_client:
            return
        if initial:
            # Only what arrives from now on is news.
            for notification in notifications:
                self._seen.add(str(notification['id']))
                self._last_id = str(notification['id'])
            return
        for notification in sorted(notifications,
                                   key=lambda n: _id_key(n['id'])):
            hook = getattr(self.window, 'note_background_notification', None)
            if hook is not None:
                hook(notification)
            self.handle(notification)


from .notifications import id_key as _id_key  # noqa: E402
from .accounts import account_key, find_account_by_key  # noqa: E402
from .mastodon import MastodonClient, summarize_error  # noqa: E402

# Kinds whose post is someone else's and can be favourited or boosted
# straight from the notification.
_STATUS_BUTTON_KINDS = ('mention', 'status')

# action name -> (HTTP method, path template, failure title)
_ACTIONS = {
    'favourite': ('POST', '/statuses/%s/favourite',
                  lambda: _('Could not favourite post')),
    'boost': ('POST', '/statuses/%s/reblog',
              lambda: _('Could not boost post')),
    'follow-back': ('POST', '/accounts/%s/follow',
                    lambda: _('Could not follow back')),
}


def _confirmation(name):
    return {'favourite': _('Post favourited'),
            'boost': _('Post boosted'),
            'follow-back': _('Followed back')}[name]


# Statuses of recent mention notifications, by (account_key, id), so
# Reply can prefill without fetching the post again.
_REPLY_STATUSES = {}
_REPLY_STATUSES_MAX = 50
# How long Reply waits for an account switch to finish, in ms.
_SWITCH_TIMEOUT_MS = 15000
_SWITCH_POLL_MS = 100


def _remember_status(key, status):
    _REPLY_STATUSES[(key, str(status['id']))] = status
    while len(_REPLY_STATUSES) > _REPLY_STATUSES_MAX:
        del _REPLY_STATUSES[next(iter(_REPLY_STATUSES))]


def install_app_actions(app):
    """Register ``app.reply``, ``app.favourite``, ``app.boost`` and
    ``app.follow-back``, the targets of the desktop notification buttons.
    Each takes ``(account_key, id)``."""
    for name in _ACTIONS:
        action = Gio.SimpleAction.new(name, GLib.VariantType.new('(ss)'))
        action.connect('activate', _on_activate, app)
        app.add_action(action)
    action = Gio.SimpleAction.new('reply', GLib.VariantType.new('(ss)'))
    action.connect('activate', _on_reply, app)
    app.add_action(action)


def _active_key(win):
    if win is None or win.client is None or win.account is None:
        return None
    return account_key(win.account)


def _on_reply(_action, parameter, app):
    key, status_id = parameter.unpack()
    win = app.ensure_window()
    win.present()
    if _active_key(win) == key:
        _reply(win, key, status_id)
        return
    from .pages import settings, show_toast
    account = find_account_by_key(settings(), key)
    if account is None or not hasattr(win, 'switch_account'):
        show_toast(win, _('That account is no longer signed in'))
        return
    win.switch_account(key)
    waited = [0]

    def check():
        if _active_key(win) == key:
            _reply(win, key, status_id)
            return GLib.SOURCE_REMOVE
        waited[0] += _SWITCH_POLL_MS
        if waited[0] >= _SWITCH_TIMEOUT_MS:
            # Translators: %s is an account's name.
            show_toast(win, _('Switch to %s to reply') % plain_name(account))
            if hasattr(win, 'show_page'):
                win.show_page('notifications')
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    if check() == GLib.SOURCE_CONTINUE:
        GLib.timeout_add(_SWITCH_POLL_MS, check)


def _reply(win, key, status_id):
    status = _REPLY_STATUSES.get((key, status_id))
    if status is not None:
        _open_reply(win, status_id, status)
        return

    def done(c, result):
        try:
            fetched = c.request_finish(result)
        except GLib.Error as error:
            from .pages import report_error
            report_error(win, _('Could not load post'), error)
            return
        _open_reply(win, status_id, fetched or {})

    win.client.request_async('GET', '/statuses/%s' % status_id,
                             callback=done)


def _open_reply(win, status_id, status):
    win.open_compose_reply(
        status_id, (status.get('account') or {}).get('acct', ''),
        status.get('visibility') or 'public')


def _main_window(app):
    for win in app.get_windows():
        if hasattr(win, 'client') and hasattr(win, 'account'):
            return win
    return None


def _on_activate(action, parameter, app):
    name = action.get_name()
    key, target_id = parameter.unpack()
    win = _main_window(app)
    if win is not None and win.client is not None \
            and win.account is not None \
            and account_key(win.account) == key:
        _run(app, win.client, name, target_id)
        return
    # The notification belongs to an account that is not the active one:
    # act as that account with its own token, without switching.
    from .pages import settings
    account = find_account_by_key(settings(), key)
    if account is None:
        _report_failure(app, name, _('That account is no longer signed in'))
        return
    client = MastodonClient(account['instance'])

    def loaded(token):
        if token is None:
            _report_failure(app, name,
                            _('The login for that account is gone from the '
                              'keyring'))
            return
        client.token = token
        _run(app, client, name, target_id)

    client.load_secret_token(account['id'], loaded)


def _run(app, client, name, target_id):
    method, path, _title = _ACTIONS[name]

    def done(c, result):
        try:
            c.request_finish(result)
        except GLib.Error as error:
            _report_failure(app, name, error.message)
            return
        win = _main_window(app)
        if win is not None and win.get_visible():
            from .pages import show_toast
            show_toast(win, _confirmation(name))

    client.request_async(method, path % target_id, callback=done)


def _report_failure(app, name, detail):
    note = Gio.Notification.new(_ACTIONS[name][2]())
    body = summarize_error(detail or '')
    if body:
        note.set_body(body)
    app.send_notification('babble-action-failed', note)
