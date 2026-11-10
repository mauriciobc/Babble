# SPDX-License-Identifier: GPL-3.0-or-later

"""The folds of a post card: the content warning with its Show/Hide
toggle, and the Show More / Show Less expansion of a long body with its
height animation.

:class:`StatusFold` is a mixin of :class:`statusrow.StatusRow` (it needs
the row as ``self``: it reads and writes the row's widgets, its ``item``
and the animation state ``StatusRow.__init__`` initialises, and its
signals are wired to the row's own methods).
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .textemoji import InlineEmojiText  # noqa: E402
from .timelinebase import _flag  # noqa: E402


class StatusFold:
    def _ensure_cw_box(self):
        """Content warning: the spoiler line with its Show/Hide toggle."""
        if self.cw_box is None:
            box = Gtk.Box(spacing=8)
            box.add_css_class('cw-bar')
            self.cw_label = InlineEmojiText(emoji_size=16, shorten_links=True)
            self.cw_label.set_hexpand(True)
            self.cw_label.add_css_class('heading')
            self.cw_label.set_valign(Gtk.Align.CENTER)
            box.append(self.cw_label)
            self.cw_toggle = Gtk.Button(valign=Gtk.Align.CENTER)
            self.cw_toggle.add_css_class('pill')
            self.cw_toggle.add_css_class('cw-toggle')
            connect_weak(self.cw_toggle, 'clicked', self._on_cw_toggle)
            box.append(self.cw_toggle)
            self.cw_box = box
            # A subclass may reparent these (ThreadPost moves everything
            # in `main` into its own column), so insert beside the
            # sibling's real parent rather than assuming `main`.
            self.reply_box.get_parent().insert_child_after(
                box, self.reply_box)
            # Only rows that have shown a content warning pay for the
            # revealer the body slides in and out of.
            revealer = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
            holder = self.guarded.get_parent()
            holder.insert_child_after(revealer, self.guarded)
            holder.remove(self.guarded)
            revealer.set_child(self.guarded)
            self.guarded_revealer = revealer
        return self.cw_box

    def _update_cw_toggle(self, spoiler, revealed):
        self.cw_toggle.set_label(_('Hide Content') if revealed
                                 else _('Show Content'))
        self.cw_toggle.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Hide content warning: %s') % spoiler if revealed
             else _('Show content warning: %s') % spoiler])

    def _on_cw_toggle(self, _button):
        item = self.item
        if item is None:
            return
        current = item.ui.get('cw_open')
        if current is None:
            current = _flag('show-spoilers')
        item.ui['cw_open'] = not current
        spoiler = (item.translation or {}).get('spoiler_text') \
            or item.status.get('spoiler_text') or ''
        self._update_cw_toggle(spoiler, not current)
        pages.reveal(self.guarded_revealer, not current, animate=True)

    def _on_expand(self, _button):
        item = self.item
        if item is None:
            return
        content = self.content
        h0 = content.get_height()
        animate = h0 > 0 and content.get_mapped()
        self._stop_body_animation()
        item.ui['expanded'] = not item.ui.get('expanded')
        self._render_body(item, self._emoji_bank)
        if not animate:
            return
        # The text view lays the new text out before the next frame;
        # measured now it still answers with the old height. Hold the
        # old height until then and size the animation from the layout.
        self._body_overflow = content.get_overflow()
        content.set_overflow(Gtk.Overflow.HIDDEN)
        content._height_override = h0
        self._body_tick = content.add_tick_callback(
            pages.weak_method(self._on_body_laid_out), h0)

    def _on_body_laid_out(self, content, _clock, h0):
        self._body_tick = None
        h1 = Gtk.TextView.do_measure(
            content, Gtk.Orientation.VERTICAL, content.get_width())[1]
        if abs(h1 - h0) < 4:
            self._clear_body_height()
            return GLib.SOURCE_REMOVE
        animation = Adw.TimedAnimation(
            widget=content, value_from=h0, value_to=h1, duration=250,
            easing=Adw.Easing.EASE_OUT_CUBIC,
            target=Adw.CallbackAnimationTarget.new(
                pages.weak_method(self._on_body_height)))
        connect_weak(animation, 'done', self._on_body_animation_done)
        self._body_animation = animation
        animation.play()
        return GLib.SOURCE_REMOVE

    def _on_body_height(self, value):
        self.content._height_override = round(value)
        self.content.queue_resize()

    def _on_body_animation_done(self, _animation):
        self._body_animation = None
        self._clear_body_height()

    def _clear_body_height(self):
        content = self.content
        if content._height_override is not None:
            content._height_override = None
            content.set_overflow(self._body_overflow)
            content.queue_resize()

    def _stop_body_animation(self):
        if self._body_tick is not None:
            self.content.remove_tick_callback(self._body_tick)
            self._body_tick = None
        animation = self._body_animation
        if animation is not None:
            self._body_animation = None
            animation.skip()
        self._clear_body_height()
