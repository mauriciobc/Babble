# SPDX-License-Identifier: GPL-3.0-or-later

"""Drawing helpers shared by the charts.

The charts are drawn in Cairo against a colour the widget fetched, so
nothing in them can respond to a stylesheet. High contrast is the case
that matters: the colour the widget hands out is already the
high-contrast one, but every chart then multiplied its alpha by a
constant, which put back exactly the contrast high contrast had taken
away. No media query reaches a ``draw_func``, so the check is made here
instead, once, rather than in each chart.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

from .i18n import gettext as _, ngettext  # noqa: E402

# How far below full strength a chart's secondary marks are drawn when
# high contrast is not asking for the full strength.
DIMMED = 0.65


def alpha(base=DIMMED, contrast=None):
    """The alpha to draw a chart's secondary mark at.

    *base* is what the chart draws at normally. Under high contrast the
    answer is full strength whatever *base* was: a translucent mark is
    exactly what high contrast exists to remove.

    *contrast* overrides the system setting; it is there so the rule can
    be tested without touching the accessibility setting, which a test
    has no business changing."""
    if contrast is None:
        contrast = Adw.StyleManager.get_default().get_high_contrast()
    return 1.0 if contrast else base


class Sparkline(Gtk.DrawingArea):
    """Tiny usage chart of a tag's history (oldest to newest)."""

    def __init__(self, history, width=72, height=28, values=None):
        """*values* -- plain numbers, oldest first -- are drawn instead of
        the ``uses`` of *history* when given."""
        super().__init__(content_width=width, content_height=height,
                         valign=Gtk.Align.CENTER)
        if values is not None:
            self.values = [int(value or 0) for value in values]
        else:
            self.values = [int(e.get('uses') or 0)
                           for e in reversed(history or [])]
        # An informative chart, so it is named rather than hidden: the
        # role below used to be PRESENTATION, which took the whole
        # series out of the accessibility tree. The description carries
        # the numbers, since a sparkline has no text to read.
        total = sum(self.values)
        peak = max(self.values) if self.values else 0
        self.update_property(
            [Gtk.AccessibleProperty.LABEL,
             Gtk.AccessibleProperty.DESCRIPTION],
            [_('Usage over time'),
             ngettext('%(total)d use, peaking at %(peak)d',
                      '%(total)d uses, peaking at %(peak)d', total)
             % {'total': total, 'peak': peak}])
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        values = self.values
        if len(values) < 2:
            return
        top = max(values) or 1
        step = width / (len(values) - 1)
        pad = 3
        color = self.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue,
                          alpha(0.75))
        cr.set_line_width(1.8)
        cr.set_line_join(1)
        for index, value in enumerate(values):
            x = index * step
            y = height - pad - (height - 2 * pad) * value / top
            if index:
                cr.line_to(x, y)
            else:
                cr.move_to(x, y)
        cr.stroke()
