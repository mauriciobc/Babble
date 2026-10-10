# SPDX-License-Identifier: GPL-3.0-or-later

"""Thread view: the focused post with its ancestors above and its replies
below, visually nested and collapsible like Phanpy's thread comments."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gettext import gettext as _, ngettext

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .mastodon import summarize_error_markup  # noqa: E402
from .timeline import COLUMN_WIDTH, StatusItem, StatusRow  # noqa: E402

MAX_NESTING = 5
# Per-level nesting indent. Kept at 12 px so a full-depth thread still
# fits the 360 px window minimum (the page measured 361 px at 16 px).
INDENT = 12
AUTO_COLLAPSE_DEPTH = 3


def push_thread(view, client, item):
    """Push a thread page for *item* (a StatusItem) onto *view*."""
    page = ThreadPage(client, item)
    nav = Adw.NavigationPage(
        title=_('Thread'),
        tag='thread-%s' % item.status_id,
        child=page)
    view.push(nav)


def _thread_row(item, client):
    row = StatusRow()
    row.bind(item, client)
    row.set_focusable(True)
    return row


class ThreadReply(Gtk.Box):
    """One descendant post. Its own replies sit under it in a
    :class:`Gtk.Expander` titled with their count, so collapsing a
    branch uses the stock disclosure widget, with a branch rail marking
    the nesting."""

    def __init__(self, item, client, depth, reply_count):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.set_valign(Gtk.Align.START)
        self.item = item

        rail = Gtk.Box(accessible_role=Gtk.AccessibleRole.PRESENTATION)
        rail.add_css_class('thread-branch')
        self.append(rail)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.append(column)
        row = _thread_row(item, client)
        # Nested replies sit flush inside their parent column; the card
        # margins would otherwise eat the depth budget.
        row.add_css_class('thread-reply')
        column.append(row)

        self.children_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0)
        # Each level nests inside its parent's box, so the indent is per
        # level; past MAX_NESTING replies stop stepping right.
        self.children_box.set_margin_start(
            INDENT if depth + 1 < MAX_NESTING else 0)
        self.expander = None
        if reply_count:
            self.expander = Gtk.Expander(
                label=ngettext('%d reply', '%d replies', reply_count)
                % reply_count,
                expanded=depth < AUTO_COLLAPSE_DEPTH)
            self.expander.add_css_class('thread-expander')
            self.expander.get_label_widget().add_css_class('caption-heading')
            self.expander.set_child(self.children_box)
            self.expander.connect('notify::expanded', self._sync_expander_child)
            self._sync_expander_child(self.expander, None)
            column.append(self.expander)

    def _sync_expander_child(self, expander, _pspec):
        """Drop nested reply height as soon as a branch is collapsed."""
        if expander is None:
            return
        expanded = expander.get_expanded()
        self.children_box.set_visible(expanded)
        self.queue_resize()
        root = self.get_root()
        if root is not None:
            root.queue_allocate()


class ThreadPage(Adw.Bin):
    """Focused status with ancestors above and nested replies below."""

    __gtype_name__ = 'BabbleThreadPage'

    def __init__(self, client, item, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.item = item
        self.status = item.status

        self.stack = Gtk.Stack()
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(self.stack)
        self.set_child(toolbar)

        self.loading_page = pages.make_loading_page(_('Loading Thread'))
        self.stack.add_named(self.loading_page, 'loading')

        self.error_page = pages.make_error_page(_('Could Not Load Thread'),
                                                self.on_retry)
        self.stack.add_named(self.error_page, 'error')

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class('timeline-list')
        self.thread_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=0)
        clamp = Adw.Clamp(maximum_size=COLUMN_WIDTH,
                          tightening_threshold=COLUMN_WIDTH)
        clamp.set_child(self.thread_box)
        scrolled.set_child(clamp)
        self.stack.add_named(scrolled, 'list')

        self.load()

    # Data

    def load(self):
        self.stack.set_visible_child_name('loading')
        self.client.status_context_async(self.status['id'],
                                         self.on_context_loaded)

    def on_retry(self, _button):
        self.load()

    def on_context_loaded(self, client, result, _user_data=None):
        try:
            ancestors, descendants = client.status_context_finish(result)
        except GLib.Error as e:
            self.error_page.set_description(
                summarize_error_markup(e.message))
            self.stack.set_visible_child_name('error')
            return
        self.build(ancestors, descendants)
        self.stack.set_visible_child_name('list')

    # Rendering

    def build(self, ancestors, descendants):
        while (child := self.thread_box.get_first_child()) is not None:
            self.thread_box.remove(child)

        # Ancestors form a single chain, oldest first, up to the focused
        # post.
        for status in ancestors:
            self.thread_box.append(
                _thread_row(StatusItem(status), self.client))

        focused = _thread_row(self.item, self.client)
        focused.add_css_class('thread-focused')
        focused.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                [_('Selected post')])
        self.thread_box.append(focused)

        self.append_descendants(self.thread_box, descendants)

    def append_descendants(self, parent_box, descendants):
        focused_id = self.status['id']
        children_of = {}
        for status in descendants:
            parent = status.get('in_reply_to_id')
            if parent is None or parent == focused_id:
                parent = None
            children_of.setdefault(parent, []).append(status)
        for children in children_of.values():
            children.sort(key=lambda s: s.get('created_at') or '')
        self.append_children(parent_box, None, 0, children_of)

    def append_children(self, parent_box, parent_id, depth, children_of):
        for status in children_of.get(parent_id, []):
            children = children_of.get(status['id'], [])
            node = ThreadReply(StatusItem(status), self.client, depth,
                               len(children))
            parent_box.append(node)
            self.append_children(node.children_box, status['id'], depth + 1,
                                 children_of)
