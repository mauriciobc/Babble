# SPDX-License-Identifier: GPL-3.0-or-later

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from . import feedback, linkrouting, uihelpers  # noqa: E402
from .api import relationships as api_relationships  # noqa: E402
from .uihelpers import reveal  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from .notificationctrl import _RELATIONSHIPS  # noqa: E402
from .mediacache import AvatarCache  # noqa: E402
from .timelinebase import COLUMN_WIDTH  # noqa: E402
from .notificationmodel import (  # noqa: E402
    _clean_name, day_heading, notification_detail, notification_links,
    type_icon)

# Stacked avatars before a "+N" counter; keep the strip inside the column.
AVATAR_STRIP_MAX = 6
TRIVIAL_TYPES = frozenset({'favourite', 'reblog', 'follow'})


class ColumnLayout(Gtk.BoxLayout):
    """Pins width-for-height (and height-for-width) to ``column_cap``.

    List item alignment measures natural width-for-height; wrapped
    labels then center at full text width. The cap lives on the layout
    manager because Gtk.Box does not re-dispatch measure from Python.
    """

    def do_measure(self, widget, orientation, for_size):
        cap = widget.column_cap
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum, natural, min_b, nat_b = Gtk.BoxLayout.do_measure(
                self, widget, orientation, for_size)
            return (min(minimum, cap), min(natural, cap), min_b, nat_b)
        if for_size > cap:
            for_size = cap
        return Gtk.BoxLayout.do_measure(self, widget, orientation,
                                        for_size)


class ColumnBox(Gtk.Box):
    """Fixed-column Gtk.Box backed by ColumnLayout."""

    def __init__(self, cap, **kwargs):
        layout = ColumnLayout()
        layout.set_orientation(kwargs.pop('orientation',
                                          Gtk.Orientation.HORIZONTAL))
        layout.set_spacing(kwargs.pop('spacing', 0))
        super().__init__()
        self.column_cap = cap
        self.set_layout_manager(layout)
        for key, value in kwargs.items():
            self.set_property(key, value)


ROW_MARGIN = 12


def make_date_heading():
    """Date group heading: title ('Today' / 'Oct 4') beside a dimmed
    weekday."""
    box = ColumnBox(COLUMN_WIDTH - 2 * ROW_MARGIN, spacing=8)
    box.set_halign(Gtk.Align.FILL)
    box.set_margin_start(ROW_MARGIN)
    box.set_margin_end(ROW_MARGIN)
    box.set_margin_top(12)
    box.set_margin_bottom(6)
    title = Gtk.Label(xalign=0.0)
    title.add_css_class('heading')
    box.append(title)
    weekday = Gtk.Label(xalign=0.0)
    weekday.add_css_class('dimmed')
    box.append(weekday)
    return box, title, weekday


class NotificationFactory(Gtk.SignalListItemFactory):

    def __init__(self, client):
        super().__init__()
        self.client = client
        # Position → row, for activation when a cluster has no single actor.
        self.rows = {}
        self.connect('setup', self.on_setup)
        self.connect('bind', self.on_bind)
        self.connect('unbind', self.on_unbind)

    def row_at(self, position):
        return self.rows.get(position)

    def on_setup(self, _factory, list_item):
        if isinstance(list_item, Gtk.ListHeader):
            box, _title, _weekday = make_date_heading()
            list_item.set_child(box)
            return
        row = NotificationRow()
        list_item.set_child(row)
        list_item.set_activatable(True)

    def on_bind(self, _factory, list_item):
        item = list_item.get_item()
        if isinstance(list_item, Gtk.ListHeader):
            box = list_item.get_child()
            title, weekday = box.get_first_child(), \
                box.get_first_child().get_next_sibling()
            heading, weekday_text = day_heading(item.date_key)
            title.set_text(heading)
            weekday.set_text(weekday_text)
            return
        row = list_item.get_child()
        row.bind(item, self.client)
        self.rows[list_item.get_position()] = row

    def on_unbind(self, _factory, list_item):
        if isinstance(list_item, Gtk.ListHeader):
            return
        self.rows.pop(list_item.get_position(), None)


from .notificationpost import NotificationPost  # noqa: E402


class NotificationRow(ColumnBox):
    """Notification card: actor avatar, type icon, grouped summary line,
    relative timestamp, a compact post snapshot for status-bearing types
    and an expander listing the people of merged clusters."""

    __gtype_name__ = 'BabbleNotificationRow'

    def __init__(self, **kwargs):
        super().__init__(cap=COLUMN_WIDTH - 2 * ROW_MARGIN,
                         orientation=Gtk.Orientation.VERTICAL,
                         spacing=0, **kwargs)
        self.set_focusable(True)
        self.add_css_class('notification-row')
        self.add_css_class('card')
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.START)
        self.set_vexpand(False)
        self.set_margin_start(ROW_MARGIN)
        self.set_margin_end(ROW_MARGIN)
        self.set_margin_bottom(ROW_MARGIN)
        self.item = None
        self.client = None
        # Recycle state, cleared by _clear_actions on every rebind.
        self._request_buttons = ()
        self._follow_button = None

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                          margin_top=12, margin_bottom=12,
                          margin_start=12, margin_end=12)

        header = Gtk.Box(spacing=12)
        self.avatar = Adw.Avatar(
            size=40, show_initials=True,
            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        header.append(self.avatar)
        summary = Gtk.Box(spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        self.icon = Gtk.Image(
            pixel_size=16, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.icon.add_css_class('dimmed')
        summary.append(self.icon)
        self.title_label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True,
                                     wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title_label.add_css_class('heading')
        summary.append(self.title_label)
        header.append(summary)
        self.time_label = Gtk.Label(valign=Gtk.Align.START)
        self.time_label.add_css_class('caption')
        self.time_label.add_css_class('dimmed')
        header.append(self.time_label)
        self.dismiss_button = Gtk.Button(
            icon_name='window-close-symbolic', valign=Gtk.Align.START,
            tooltip_text=_('Dismiss Notification'))
        self.dismiss_button.add_css_class('flat')
        self.dismiss_button.add_css_class('circular')
        uihelpers.set_accessible_label(self.dismiss_button,
                                   _('Dismiss Notification'))
        connect_weak(self.dismiss_button, 'clicked', self._on_dismiss_clicked)
        header.append(self.dismiss_button)
        content.append(header)

        self.detail_label = Gtk.Label(xalign=0.0, wrap=True, visible=False,
                                      wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.detail_label.add_css_class('notification-detail')
        content.append(self.detail_label)

        # Built on the first bind that needs them: most notifications are
        # a single actor with no post (follows, signups, reports), and the
        # page opens several rows in one frame. See _ensure_post and
        # _ensure_cluster.
        self.post = None
        self.avatar_stack = None
        self.people_box = None
        self.people_revealer = None
        self.cluster_expander = None

        self.actions_box = Gtk.Box(spacing=8, halign=Gtk.Align.START,
                                   visible=False)
        self.actions_box.add_css_class('notification-actions')
        content.append(self.actions_box)

        self._content = content
        self.append(content)

    def _ensure_post(self):
        """The embedded post snapshot, built the first time a row holds a
        status and kept for the row's later binds."""
        if self.post is None:
            self.post = NotificationPost()
            self.post.set_visible(False)
            self._content.insert_child_after(self.post, self.detail_label)
        return self.post

    def _ensure_cluster(self):
        """The people strip and its expander, built the first time a row
        holds a merged notification and kept for later binds."""
        if self.cluster_expander is not None:
            return
        # The strip must not force a row wider than the column: cap the
        # visible avatars and count the rest, instead of letting a
        # nine-person cluster set a 352 px minimum on a 360 px window.
        self.avatar_stack = Gtk.Box()
        self.avatar_stack.add_css_class('notification-avatar-stack')
        self.people_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=6, margin_top=6)
        self.people_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            child=self.people_box)
        self.cluster_expander = Gtk.Expander(
            label_widget=self.avatar_stack, child=self.people_revealer,
            visible=False)
        connect_weak(self.cluster_expander, 'notify::expanded',
                     self._on_expanded_changed)
        # GtkExpander's gesture on the title widget never claims its
        # sequence, so a click on the avatars or the arrow also reached
        # the list item's activation gesture and opened actors[0]. Claim
        # title clicks in capture phase and toggle here instead: a
        # claimed sequence cancels the expander's own handler.
        self._title_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        self._title_click.set_propagation_phase(
            Gtk.PropagationPhase.CAPTURE)
        connect_weak(self._title_click, 'pressed',
                     self._on_title_pressed)
        connect_weak(self._title_click, 'released',
                     self._on_title_released)
        connect_weak(self._title_click, 'cancel', self._on_title_cancel)
        self.cluster_expander.add_controller(self._title_click)
        self._title_claimed = False
        self._content.insert_child_after(self.cluster_expander,
                                         self.actions_box)

    def _on_dismiss_clicked(self, _button):
        from .notifications import NotificationsPage
        page = self.get_ancestor(NotificationsPage)
        if page is not None and self.item is not None:
            page.dismiss(self.item)

    def _in_cluster_title(self, x, y):
        """A point on the expander's title (arrow or avatar strip) but
        not in the revealed people list; the gesture sits on the whole
        expander, child included."""
        picked = self.cluster_expander.pick(x, y, Gtk.PickFlags.DEFAULT)
        while picked is not None:
            if picked is self.people_revealer:
                return False
            if picked is self.cluster_expander:
                return True
            picked = picked.get_parent()
        return False

    def _on_title_pressed(self, gesture, _n_press, x, y):
        if self._title_claimed or not self._in_cluster_title(x, y):
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._title_claimed = True

    def _on_title_released(self, _gesture, _n_press, _x, _y):
        if not self._title_claimed:
            return
        self._title_claimed = False
        self.cluster_expander.set_expanded(
            not self.cluster_expander.get_expanded())

    def _on_title_cancel(self, _gesture, _sequence):
        self._title_claimed = False

    def _on_expanded_changed(self, expander, _pspec):
        expanded = expander.get_expanded()
        reveal(self.people_revealer, expanded, animate=expanded)

    def _person_row(self, actor, name, kind):
        """One named person row under the expander: action icon and name.
        A button, so it opens that person -- and so its click is claimed
        and cannot also activate the card behind it."""
        person = Gtk.Box(spacing=8)
        person.append(Gtk.Image(
            icon_name=type_icon(kind),
            accessible_role=Gtk.AccessibleRole.PRESENTATION))
        person.append(Gtk.Label(label=name, xalign=0.0, hexpand=True,
                                ellipsize=Pango.EllipsizeMode.END))
        button = Gtk.Button(child=person)
        button.add_css_class('flat')
        uihelpers.set_accessible_label(button, name)
        connect_weak(button, 'clicked', self._on_person_clicked, actor)
        return button

    def _on_person_clicked(self, _button, actor):
        root = self.get_root()
        view = getattr(root, 'navigation_view', None) if root else None
        if view is None:
            return
        from .profile import push_profile
        push_profile(view, self.client, actor)

    def _populate_people(self, item):
        """Stacked avatars and named person rows behind the expander.

        Built on first cluster bind; a later single-actor bind hides the
        widgets and clears the people they named.
        """
        is_cluster = len(item.notifications) > 1
        if self.cluster_expander is not None:
            while (child := self.people_box.get_first_child()) is not None:
                self.people_box.remove(child)
            while (child := self.avatar_stack.get_first_child()) is not None:
                self.avatar_stack.remove(child)
            self.cluster_expander.set_visible(is_cluster)
            self.cluster_expander.set_expanded(False)
            self.cluster_expander.reset_property(
                Gtk.AccessibleProperty.LABEL)
            reveal(self.people_revealer, False)
        self.avatar.set_visible(not is_cluster)
        if not is_cluster:
            return
        self._ensure_cluster()
        self.cluster_expander.set_visible(True)
        names = []
        actors = list(zip(item.actors, item.kinds, strict=False))
        shown = actors[:AVATAR_STRIP_MAX]
        for actor, kind in shown:
            name = _clean_name(actor)
            names.append(name)
            avatar = Adw.Avatar(
                size=32, text=name, show_initials=True,
                accessible_role=Gtk.AccessibleRole.PRESENTATION)
            AvatarCache.load(avatar, actor)
            slot = Gtk.Overlay(child=avatar)
            badge = Gtk.Image.new_from_icon_name(type_icon(kind))
            badge.set_pixel_size(8)
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.END)
            badge.add_css_class('notification-badge')
            slot.add_overlay(badge)
            self.avatar_stack.append(slot)
            self.people_box.append(self._person_row(actor, name, kind))
        remaining = len(actors) - len(shown)
        if remaining > 0:
            more = Gtk.Label(label='+%d' % remaining)
            more.add_css_class('notification-avatar-more')
            more.set_valign(Gtk.Align.CENTER)
            more.update_property(
                [Gtk.AccessibleProperty.LABEL],
                [ngettext('%d more person', '%d more people', remaining)
                 % remaining])
            self.avatar_stack.append(more)
        for actor, kind in actors[len(shown):]:
            name = _clean_name(actor)
            names.append(name)
            self.people_box.append(self._person_row(actor, name, kind))
        people = ', '.join(names)
        uihelpers.set_accessible_label(self.cluster_expander, people)

    # Inline actions

    def _clear_actions(self):
        """Empty the actions box and forget recycled button refs."""
        while (child := self.actions_box.get_first_child()) is not None:
            self.actions_box.remove(child)
        self._request_buttons = ()
        self._follow_button = None

    def _action_button(self, label, callback, *extra, css=None):
        button = Gtk.Button(label=label, use_underline=True)
        button.add_css_class('pill')
        if css:
            button.add_css_class(css)
        connect_weak(button, 'clicked', callback, *extra)
        self.actions_box.append(button)
        return button

    def _action_note(self, text):
        label = Gtk.Label(label=text, xalign=0.0)
        label.add_css_class('dimmed')
        self.actions_box.append(label)

    def _bind_extras(self, item, client):
        """Detail text and inline actions (Accept/Reject, Follow Back,
        View Report…) of the types that have them."""
        self._clear_actions()
        instance = getattr(client, 'instance', '') or ''
        detail = notification_detail(item, instance)
        self.detail_label.set_visible(bool(detail))
        self.detail_label.set_text(detail or '')
        if set(item.kinds) <= TRIVIAL_TYPES:
            self.add_css_class('can-be-dimmed')
        else:
            self.remove_css_class('can-be-dimmed')
        single = len(item.notifications) == 1
        kind = item.kind
        if kind == 'follow_request' and single:
            self._add_request_buttons(item)
        elif kind == 'follow' and single:
            self._add_follow_back(item)
        elif kind == 'admin.report':
            self._action_button(_('_View Reports'), self._on_view_reports)
        elif kind == 'annual_report':
            self._action_button(_('_View'), self._on_view_report_year,
                                css='suggested-action')
        for label, uri in notification_links(item, instance):
            self._action_button(label, self._on_link_clicked, uri)
        self.actions_box.set_visible(
            self.actions_box.get_first_child() is not None)

    def _on_view_reports(self, _button):
        from .admin import present_admin
        present_admin(self, self.client)

    def _on_link_clicked(self, _button, uri):
        linkrouting.launch_external(self, uri)

    def _on_view_report_year(self, _button):
        from .wrapped import present_wrapped
        report = self.item.payload.get('annual_report') or {}
        present_wrapped(self, self.client, report.get('year'))

    def _add_request_buttons(self, item):
        state = item.request_state
        if state == 'accepted':
            self._action_note(_('Request accepted'))
            return
        if state == 'rejected':
            self._action_note(_('Request rejected'))
            return
        reject = self._action_button(
            _('_Reject'), self._on_request_clicked, item, 'reject')
        accept = self._action_button(
            _('_Accept'), self._on_request_clicked, item, 'authorize',
            css='suggested-action')
        self._request_buttons = (reject, accept)

    def _on_request_clicked(self, _button, item, action):
        self._answer_request(item, action)

    def _answer_request(self, item, action):
        """POST /api/v1/follow_requests/:account_id/(authorize|reject)."""
        client = self.client
        # Held locally: by the time the reply lands the row may have been
        # rebound, and re-enabling a button that belongs to a different
        # item is worse than leaving this one greyed.
        buttons = self._request_buttons
        for button in buttons:
            button.set_sensitive(False)

        def done(_client, result, _data=None):
            try:
                api_relationships.answer_request_finish(client, result)
            except GLib.Error as error:
                for button in buttons:
                    button.set_sensitive(True)
                feedback.report_error(self, _('Could not answer the request'), error)
                return
            item.request_state = 'accepted' if action == 'authorize' \
                else 'rejected'
            if self.item is item:
                self._bind_extras(item, client)

        api_relationships.answer_request_async(
            client, item.actors[0]['id'], action, callback=done)


    def _add_follow_back(self, item):
        actor = item.actors[0]
        client = self.client
        key = (getattr(client, 'instance', ''), actor.get('id'))
        relationship = _RELATIONSHIPS.get(key)
        if relationship is None:
            # The page asked for it when the notification arrived; the
            # row is re-emitted once the answer is in.
            return
        if relationship.get('following'):
            self._action_note(_('Following'))
        elif relationship.get('requested'):
            self._action_note(_('Follow request sent'))
        elif relationship.get('blocking') or relationship.get('blocked_by'):
            return
        else:
            self._follow_button = self._action_button(
                _('_Follow Back'), self._on_follow_back_clicked,
                item, actor, key, css='suggested-action')

    def _on_follow_back_clicked(self, _button, item, actor, key):
        self._follow_back(item, actor, key)

    def _follow_back(self, item, actor, key):
        """POST /api/v1/accounts/:id/follow."""
        client = self.client
        # Held locally for the same reason as _answer_request.
        button = self._follow_button
        if button is None:
            return
        button.set_sensitive(False)

        def done(_client, result, _data=None):
            try:
                relationship = api_relationships.follow_finish(client, result)
            except GLib.Error as error:
                if self.item is item:
                    self._bind_extras(item, client)
                feedback.report_error(self, _('Could not follow'), error)
                return
            _RELATIONSHIPS[key] = relationship
            if self.item is item:
                self._bind_extras(item, client)

        api_relationships.follow_async(client, actor['id'], callback=done)

    def bind(self, item, client):
        self.item = item
        self.client = client
        actor = item.actors[0]
        self.avatar.set_text(_clean_name(actor))
        AvatarCache.load(self.avatar, actor)
        self.icon.set_from_icon_name(type_icon(item.kind))
        self.title_label.set_text(item.title)
        self.time_label.set_text(item.timestamp)
        has_status = item.status_item is not None
        if has_status:
            post = self._ensure_post()
            post.set_visible(True)
            post.bind(item.status_item)
        elif self.post is not None:
            self.post.set_visible(False)
            self.post.clear()
        self._populate_people(item)
        self._bind_extras(item, client)
        self.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('%(title)s, %(time)s') % {'title': item.title,
                                         'time': item.timestamp}])

