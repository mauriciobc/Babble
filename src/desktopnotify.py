# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop notifications for new Mastodon notifications.

Notifications arrive from the streaming socket; while that is down the
notifier polls ``GET /api/v1/notifications`` itself. Nothing is shown
while the window is focused (the Notifications page already is the
place for them), only the types ticked in ``desktop-notification-types``
are announced, and with ``group-desktop-notifications`` notifications of
one type about one post replace each other instead of piling up.
"""

import html
import re
from html.parser import HTMLParser

import gi

gi.require_version('Gtk', '4.0')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import GLib, Gio  # noqa: E402

from .accounts import plain_name, strip_shortcodes  # noqa: E402

POLL_SECONDS = 60
BODY_LIMIT = 200
_BLOCK_TAGS = {'p', 'br', 'div', 'li'}

# type -> (one actor, several actors); '%s' is the actor list.
TITLES = {
    'mention': (_('%s mentioned you'), _('%s mentioned you')),
    'reblog': (_('%s boosted your post'), _('%s boosted your post')),
    'favourite': (_('%s favorited your post'), _('%s favorited your post')),
    'follow': (_('%s now follows you'), _('%s now follow you')),
    'follow_request': (_('%s wants to follow you'),
                       _('%s want to follow you')),
    'poll': (_('%s’s poll has ended'), _('%s’s polls have ended')),
    'status': (_('%s posted'), _('%s posted')),
    'update': (_('%s edited a post'), _('%s edited a post')),
    'quote': (_('%s quoted your post'), _('%s quoted your post')),
    'quoted_update': (_('%s edited a post you quoted'),
                      _('%s edited a post you quoted')),
    'admin.sign_up': (_('%s signed up'), _('%s signed up')),
    'admin.report': (_('%s reported a post'), _('%s reported posts')),
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS and self.parts:
            self.parts.append('\n')

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(markup):
    """Plain text of a status ``content`` fragment."""
    parser = _TextExtractor()
    try:
        parser.feed(markup or '')
        parser.close()
    except Exception:
        return html.unescape(re.sub(r'<[^>]+>', ' ', markup or ''))
    return re.sub(r'[ \t]*\n[ \t\n]*', '\n', ''.join(parser.parts)).strip()


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
    one, many = TITLES.get(kind, (_('%s sent a notification'),) * 2)
    template = ngettext(one, many, len(names)) if one != many else one
    return template % actor_list(names)


class DesktopNotifier:
    """Turns notification JSON into Gio.Notification objects.

    ``window`` supplies ``client``, ``settings``, ``is_active()`` and
    ``get_visible()``; the notifier looks them up lazily so one instance
    survives account switches (``reset()`` forgets the old account)."""

    def __init__(self, window, application=None):
        self.window = window
        self._application = application
        self._groups = {}
        self._seen = set()
        self._last_id = None
        self._poll_source = 0
        self._polling_client = None

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
        note.set_default_action_and_target_value(
            'app.open-notifications', GLib.Variant('s', nid))
        icon = self._avatar_icon(notification.get('account') or {})
        if icon is not None:
            note.set_icon(icon)
        app.send_notification(platform_id, note)
        return note

    @staticmethod
    def _avatar_icon(account):
        from .timeline import AvatarCache
        url = account.get('avatar') or account.get('avatar_static') or ''
        return getattr(AvatarCache, '_textures', {}).get(url)

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
        if self._last_id is None:
            self._poll(initial=True)
        self._poll_source = GLib.timeout_add_seconds(
            POLL_SECONDS, self._on_poll_tick)

    def stop_polling(self):
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0
        self._polling_client = None

    def _on_poll_tick(self):
        if self.window.client is not self._polling_client:
            self._poll_source = 0
            return GLib.SOURCE_REMOVE
        if self._polling_client is not None \
                and self._polling_client.rate_limited():
            # The instance asked us to back off; keep the timer but do
            # not spend another request until the limit expires.
            return GLib.SOURCE_CONTINUE
        self._poll()
        return GLib.SOURCE_CONTINUE

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
            self.handle(notification)


from .notifications import id_key as _id_key  # noqa: E402
