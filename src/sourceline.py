# SPDX-License-Identifier: GPL-3.0-or-later

"""The line above a card's author header saying why the post is there.

A boosted post names its booster; a post in Home that arrived through a
followed hashtag, from someone the viewer does not follow, names the
hashtag.  A boost wins outright: a boosted post is never also tagged,
because the booster put it in front of the viewer, not the tag.
"""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk, Pango  # noqa: E402

from .i18n import gettext as _  # noqa: E402
from .textemoji import InlineEmojiText, emoji_bank  # noqa: E402

TAG_ICON = 'babble-hashtag-symbolic'


class SourceLine(Gtk.Box):
    """Byline above a card header: booster, or the followed hashtag a
    Home post came in through.  Hidden when neither applies."""

    __gtype_name__ = 'BabbleSourceLine'

    def __init__(self, boost_icon):
        super().__init__(spacing=6)
        self._boost_icon = boost_icon
        self._label_kind = None
        self._label = None
        self._icon = Gtk.Image(
            pixel_size=12, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.append(self._icon)
        self.set_visible(False)

    def _ensure_label(self, kind):
        """Mount a label of *kind*, replacing one of the other kind.

        Both kinds live in this single slot, so a recycled row can flip
        between the boost byline and the hashtag line without stacking
        children."""
        if self._label_kind == kind:
            return self._label
        if self._label is not None:
            self.remove(self._label)
        if kind == 'boost':
            label = InlineEmojiText(emoji_size=14, single_line=True)
            label.set_can_target(False)
        else:
            label = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END,
                              hexpand=True)
        label.add_css_class('caption-heading')
        label.add_css_class('dimmed')
        self.append(label)
        self._label_kind = kind
        self._label = label
        return label

    def show_for(self, item):
        """Show the boost byline, else the followed-hashtag byline, else
        hide.  Safe to call repeatedly for the same item."""
        if item.is_boost:
            self._icon.set_from_icon_name(self._boost_icon)
            self._icon.remove_css_class('dimmed')
            self._icon.add_css_class('boost-accent')
            label = self._ensure_label('boost')
            label.set_segments(
                [{'text': _('%s boosted') % item.boost_line, 'href': None}],
                emoji_bank(item.boosted_by))
            self.set_visible(True)
            return
        tag = item.ui.get('source_tag')
        if not tag:
            self.set_visible(False)
            return
        self._icon.set_from_icon_name(TAG_ICON)
        self._icon.remove_css_class('boost-accent')
        self._icon.add_css_class('dimmed')
        label = self._ensure_label('tag')
        label.set_label(tag)
        # Translators: byline above a post that arrived through a
        # followed hashtag; %s is the hashtag name without the leading
        # '#'.
        label.set_tooltip_text(_('From hashtag %s') % tag)
        label.update_property([Gtk.AccessibleProperty.LABEL],
                              ['#' + tag])
        self.set_visible(True)
