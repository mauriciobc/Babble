# SPDX-License-Identifier: GPL-3.0-or-later

"""Thread view: the focused post with its ancestors above and its replies
below, visually nested and collapsible like Phanpy's thread comments."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import filterrules  # noqa: E402
from . import pages  # noqa: E402
from .mastodon import summarize_error_markup  # noqa: E402
from .timelinebase import COLUMN_WIDTH  # noqa: E402
from .timelinedata import StatusItem  # noqa: E402
from .statusrow import StatusRow  # noqa: E402

MAX_NESTING = 5
# Per-level nesting indent. Kept at 12 px so a full-depth thread still
# fits the 360 px window minimum (the page measured 361 px at 16 px).
INDENT = 12
AUTO_COLLAPSE_DEPTH = 3


def push_thread(view, client, item):
    """Push a thread page for *item* (a StatusItem) onto *view*."""
    window = pages.window_of(view)
    # A thread opened inside the profile sheet has its own navigation
    # view; the window's sidebar controls do not belong there.
    in_window = window is not None and getattr(
        window, 'navigation_view', None) is view
    page = ThreadPage(
        client, item,
        sidebar_control=getattr(window, 'make_sidebar_control', None)
        if in_window else None)
    nav = Adw.NavigationPage(
        title=_('Thread'),
        tag='thread-%s' % item.status_id,
        child=page)
    view.push(nav)


class ThreadPost(StatusRow):
    """A post laid out the way a thread reads it: the avatar in a column
    of its own, with a line under it down to the next post of the chain,
    and the text beside it rather than under it. Several of these share
    one card (``.thread-card``)."""

    __gtype_name__ = 'BabbleThreadPost'

    def __init__(self, focused=False, **kwargs):
        # Only the selected post keeps its action bar on regardless of
        # the setting, as in the timeline.
        super().__init__(detail=focused, **kwargs)
        self.add_css_class('thread-post')
        # Shown when the parent post sits right above, where the line
        # says what "Replying to" would.
        self.parent_shown = False

        main = self.main
        for margin in ('top', 'bottom', 'start', 'end'):
            getattr(main, 'set_margin_' + margin)(0)
        self.avatar.get_parent().remove(self.avatar)
        self.avatar.set_size(40)
        self.avatar.set_valign(Gtk.Align.START)
        self.avatar.set_cursor_from_name('pointer')
        # The name button is the keyboard path to the profile; the
        # avatar is only a larger pointer target for it.
        click = Gtk.GestureClick()
        pages.connect_weak(click, 'released', self._on_avatar_released)
        self.avatar.add_controller(click)

        # Name and handle on one line, the handle wrapping below the
        # name when both do not fit.
        names = self._name_line.get_parent()
        names.remove(self._name_line)
        names.remove(self.handle_label)
        self._name_line.set_hexpand(False)
        self.handle_label.set_hexpand(False)
        self.handle_label.set_valign(Gtk.Align.CENTER)
        wrap = Adw.WrapBox(child_spacing=6)
        wrap.append(self._name_line)
        wrap.append(self.handle_label)
        names.append(wrap)

        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=4, hexpand=True)
        self.column.add_css_class('thread-post-column')
        while (child := main.get_first_child()) is not None:
            main.remove(child)
            self.column.append(child)

        # The line runs through the space between two posts: from below
        # one avatar, through that post's bottom padding (a negative
        # margin) and the next post's top padding (its lead-in), to the
        # next avatar.
        gutter = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.lead_in = Gtk.Box(
            halign=Gtk.Align.CENTER,
            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.lead_in.add_css_class('thread-line-in')
        gutter.append(self.lead_in)
        gutter.append(self.avatar)
        self.line = Gtk.Box(vexpand=True, halign=Gtk.Align.CENTER,
                            visible=False,
                            accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.line.add_css_class('thread-line')
        gutter.append(self.line)

        layout = Gtk.Box(spacing=12)
        layout.append(gutter)
        layout.append(self.column)
        main.append(layout)
        self._focused = focused
        self._place_actions(False)

    def set_linked(self, linked):
        """Draw the line down to the next post of the chain."""
        self.line.set_visible(linked)
        if linked:
            self.add_css_class('linked')
        else:
            self.remove_css_class('linked')
        self._place_actions(linked)

    def _place_actions(self, linked):
        # The selected post's bar takes the card's whole width, so its
        # counts still fit one line at the narrowest window; when the
        # chain goes on below, it stays beside the line instead of
        # cutting through it.
        parent = self.column if linked or not self._focused else self.main
        if self.actions.get_parent() is not parent:
            self.actions.get_parent().remove(self.actions)
            parent.append(self.actions)

    def _bind_reply_line(self, item):
        super()._bind_reply_line(item)
        if self.parent_shown:
            self.reply_box.set_visible(False)

    def _on_avatar_released(self, _gesture, _n, _x, _y):
        self._on_profile_clicked(None)


def _thread_row(item, client, parent_shown=False, focused=False):
    row = ThreadPost(focused=focused)
    row.parent_shown = parent_shown
    # A thread is its own Filter#context: a rule scoped to one does not
    # reach into the conversation a post is read in.
    row.filter_context = filterrules.THREAD
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
        # A top-level reply is a card of its own; deeper ones sit inside
        # it, stepped right along the rail.
        if depth == 0:
            self.add_css_class('thread-card')
            rail.set_visible(False)
        else:
            self.add_css_class('thread-nested')
        # Every reply's parent is the post above its card or column.
        row = _thread_row(item, client, parent_shown=True)
        if depth:
            # Smaller avatars leave nested replies more of the width.
            row.avatar.set_size(32)
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
            label = self.expander.get_label_widget()
            label.add_css_class('caption-heading')
            # The title row is a pill sized to its text, not the width.
            label.get_parent().set_halign(Gtk.Align.START)
            self.expander.set_child(self.children_box)
            pages.connect_weak(self.expander, 'notify::expanded',
                               self._sync_expander_child)
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

    def __init__(self, client, item, sidebar_control=None, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.item = item
        self.status = item.status

        self.stack = Gtk.Stack()
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        if callable(sidebar_control):
            header.pack_start(sidebar_control())
        toolbar.add_top_bar(header)
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
                summarize_error_markup(e))
            self.stack.set_visible_child_name('error')
            return
        self.build(ancestors, descendants)
        self.stack.set_visible_child_name('list')

    # Rendering

    def build(self, ancestors, descendants):
        while (child := self.thread_box.get_first_child()) is not None:
            self.thread_box.remove(child)

        # Ancestors form a single chain, oldest first, up to the focused
        # post, all in one card with a line from avatar to avatar.
        chain = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        chain.add_css_class('thread-card')
        for status in ancestors:
            row = _thread_row(StatusItem(status), self.client,
                              parent_shown=chain.get_first_child() is not None)
            row.set_linked(True)
            chain.append(row)

        focused = _thread_row(self.item, self.client,
                              parent_shown=bool(ancestors), focused=True)
        focused.add_css_class('thread-focused')
        focused.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                                [_('Selected post')])
        chain.append(focused)
        self.thread_box.append(chain)

        # While a post has a single reply the conversation goes on in the
        # same card; where it branches, the replies nest below it.
        children_of = self.children_of(descendants)
        parent_id, tail = None, focused
        while len(children_of.get(parent_id, ())) == 1:
            status = children_of[parent_id][0]
            tail.set_linked(True)
            tail = _thread_row(StatusItem(status), self.client,
                               parent_shown=True)
            chain.append(tail)
            parent_id = status['id']
        self.append_children(self.thread_box, parent_id, 0, children_of)

    def children_of(self, descendants):
        """Descendants by the id they reply to; replies to the focused
        post are under None, each list oldest first."""
        focused_id = self.status['id']
        children_of = {}
        for status in descendants:
            parent = status.get('in_reply_to_id')
            if parent is None or parent == focused_id:
                parent = None
            children_of.setdefault(parent, []).append(status)
        for children in children_of.values():
            children.sort(key=lambda s: s.get('created_at') or '')
        return children_of

    def append_children(self, parent_box, parent_id, depth, children_of):
        for status in children_of.get(parent_id, []):
            children = children_of.get(status['id'], [])
            node = ThreadReply(StatusItem(status), self.client, depth,
                               len(children))
            parent_box.append(node)
            self.append_children(node.children_box, status['id'], depth + 1,
                                 children_of)
