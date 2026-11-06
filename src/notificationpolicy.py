# SPDX-License-Identifier: GPL-3.0-or-later
"""Notification settings dialog: server policy and type visibility."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .api import notifications as api_notifications  # noqa: E402
from .mastodon import summarize_error_markup  # noqa: E402
from .notificationcopy import (FILTER_NOTIFICATION_TYPES,  # noqa: E402
                               type_offered)
from .notificationmodel import type_icon  # noqa: E402
from .pages import connect_weak, settings, show_toast  # noqa: E402


EXCLUDED_TYPES_KEY = 'notification-excluded-types'

# (type, label) entries of the header Filter submenu and the settings
# dialog.
NOTIFICATION_TYPES = FILTER_NOTIFICATION_TYPES

POLICY_FIELDS = [
    ('for_not_following', _('People You Don’t Follow'),
     _('Accounts you do not follow')),
    ('for_not_followers', _('People Not Following You'),
     _('Accounts that do not follow you, or followed you less than three '
       'days ago')),
    ('for_new_accounts', _('New Accounts'),
     _('Accounts created within the last 30 days')),
    ('for_private_mentions', _('Unsolicited Private Mentions'),
     _('Unless in reply to your own mention or sent by someone you follow')),
    ('for_limited_accounts', _('Moderated Accounts'),
     _('Accounts limited by the server moderators')),
    ('for_bots', _('Bots'), _('Accounts marked as automated')),
]

POLICY_CHOICES = [
    ('accept', _('Accept')),
    ('filter', _('Filter')),
    ('drop', _('Ignore')),
]


class NotificationPolicyDialog(pages.ToastDialog):
    """Edits ``GET/PATCH /api/v2/notifications/policy`` (what to do with
    notifications from each kind of sender: accept, filter into the
    requests inbox or drop) and the per-type visibility switches. The
    policy is saved when the dialog closes, only if something changed."""

    __gtype_name__ = 'BabbleNotificationPolicyDialog'

    def __init__(self, client, parent=None):
        super().__init__(_('Notification Settings'), 480, 620)
        self.client = client
        self._parent = parent
        self.policy = None
        self.filter_rows = {}
        self.type_rows = {}

        self.filters_group = Adw.PreferencesGroup(
            title=_('Filter Notifications From'),
            description=_('Loading…'))
        for key, title, subtitle in POLICY_FIELDS:
            row = Adw.ComboRow(
                title=title, subtitle=subtitle, visible=False,
                model=Gtk.StringList.new(
                    [label for _code, label in POLICY_CHOICES]))
            self.filter_rows[key] = row
            self.filters_group.add(row)
        self.page.add(self.filters_group)

        types_group = Adw.PreferencesGroup(
            title=_('Show Notifications For'))
        excluded = set(settings().get_strv(EXCLUDED_TYPES_KEY))
        for kind, label in NOTIFICATION_TYPES:
            row = Adw.SwitchRow(title=label, active=kind not in excluded,
                                visible=type_offered(self.client, kind))
            row.add_prefix(Gtk.Image(icon_name=type_icon(kind)))
            connect_weak(row, 'notify::active', self._on_type_switched, kind)
            self.type_rows[kind] = row
            types_group.add(row)
        self.page.add(types_group)

        connect_weak(self, 'closed', self._on_closed)
        self.load()

    def _on_closed(self, _dialog):
        self.save()

    # Policy

    def load(self):
        api_notifications.policy_async(self.client, callback=self.on_loaded)

    def on_loaded(self, client, result, _user_data=None):
        try:
            policy = api_notifications.policy_finish(client, result)
        except GLib.Error as error:
            self.filters_group.set_description(
                _('This server does not support filtering notifications')
                if getattr(error, 'code', 0) == 404
                else summarize_error_markup(error))
            return
        self.policy = policy
        self.filters_group.set_description(_(
            'Filtered notifications wait in a separate inbox until you '
            'accept them. Ignored ones are discarded.'))
        codes = [code for code, _label in POLICY_CHOICES]
        for key, row in self.filter_rows.items():
            if key not in policy:
                continue
            value = policy.get(key)
            row.set_selected(codes.index(value) if value in codes else 0)
            row.set_visible(True)

    def changed_payload(self):
        """The policy fields whose selection differs from the loaded
        values (empty before the policy has loaded)."""
        if self.policy is None:
            return {}
        payload = {}
        for key, row in self.filter_rows.items():
            if key not in self.policy:
                continue
            choice = POLICY_CHOICES[row.get_selected()][0]
            if choice != self.policy.get(key):
                payload[key] = choice
        return payload

    def save(self):
        """PATCH /api/v2/notifications/policy with the changed fields."""
        payload = self.changed_payload()
        if not payload:
            return
        parent = self._parent

        def saved(_data=None):
            # Reported on the window behind the dialog: a toast raised on
            # the dialog itself would be covered when it closes.
            if parent is not None:
                show_toast(parent, _('Notification settings updated'))

        pages.call_api(
            self, self.client,
            lambda callback: api_notifications.update_policy_async(
                self.client, payload, callback=callback),
            on_done=saved,
            error_heading=_('Could not save notification settings'),
            finish=api_notifications.update_policy_finish)

    # Visible types

    def _on_type_switched(self, row, _pspec, kind):
        _store_type_visibility(kind, row.get_active())


def _store_type_visibility(kind, visible):
    """Persist one type's visibility in the exclusion list."""
    current = settings().get_strv(EXCLUDED_TYPES_KEY)
    excluded = [k for k in current if k != kind]
    if not visible:
        excluded.append(kind)
    if excluded != list(current):
        settings().set_strv(EXCLUDED_TYPES_KEY, excluded)


def present_notification_policy(parent, client):
    """Show the notification policy dialog over *parent*."""
    dialog = NotificationPolicyDialog(client, parent)
    dialog.present(parent)
    return dialog
