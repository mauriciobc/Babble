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

from gi.repository import Adw  # noqa: E402

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
