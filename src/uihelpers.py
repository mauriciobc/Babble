# SPDX-License-Identifier: GPL-3.0-or-later

"""Small generic widget helpers: reveal and flash animations, section
labels, keyboard activation, accessible labels, icon buttons and
child clearing. They know nothing of pages, accounts or the app."""

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

# Motion

REVEAL_MS = 200


def reveal(revealer, shown, animate=False):
    """Show or hide *revealer*'s child, sliding only when *animate*.

    List rows are rebound when the view recycles them, so binding has to
    land on the final state at once; only a user's toggle animates."""
    revealer.set_transition_duration(REVEAL_MS if animate else 0)
    revealer.set_reveal_child(shown)


def flash_class(widget, css_class, ms=300):
    """Add *css_class* to *widget* for *ms* milliseconds, to play a CSS
    keyframe animation once. A state selector such as ``:checked`` would
    replay it whenever a recycled row is bound to a checked item."""
    # A flash restarted before the last one ended must not be cut short
    # by the earlier timeout: only the latest one removes the class.
    flashes = getattr(widget, '_babble_flashes', None)
    if flashes is None:
        flashes = widget._babble_flashes = {}
    token = flashes.get(css_class, 0) + 1
    flashes[css_class] = token
    widget.remove_css_class(css_class)
    widget.add_css_class(css_class)
    target = widget.weak_ref()

    def done():
        owner = target()
        if owner is not None:
            latest = getattr(owner, '_babble_flashes', {}).get(css_class)
            if latest in (None, token):
                owner.remove_css_class(css_class)
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(ms, done)


def clear_children(widget):
    """Remove every child of *widget* before it is filled again."""
    while (child := widget.get_first_child()) is not None:
        widget.remove(child)


# Small widgets

def section_label(text):
    label = Gtk.Label(label=text, xalign=0.0, wrap=True,
                      ellipsize=Pango.EllipsizeMode.NONE)
    label.add_css_class('heading')
    return label


def activate_on_key(widget, callback, *args):
    """Call ``callback(*args)`` when Enter or space is pressed while
    *widget* itself has the keyboard focus: the keyboard half of a
    ``GtkGestureClick`` on something that is not a button.

    The caller makes *widget* focusable and gives it a focus ring. A key
    that bubbles up from a button or text view inside *widget* is left
    alone. *callback* must not hold *widget* strongly (use
    :func:`weak_method` or a GObject weak reference): the controller
    belongs to the widget, so that would be the cycle through C that
    :func:`connect_weak` prevents.
    """
    def pressed(controller, keyval, _keycode, _state):
        if (keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space)
                and controller.get_widget().is_focus()):
            callback(*args)
            return True
        return False

    keys = Gtk.EventControllerKey()
    keys.connect('key-pressed', pressed)
    widget.add_controller(keys)


def set_accessible_label(widget, text):
    """Give *widget* *text* as both its tooltip and its accessible label.

    A tooltip does not set the accessible name, and an icon-only control
    has no text of its own, so it needs both. Where the two should differ,
    set them separately instead.
    """
    widget.set_tooltip_text(text)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


def icon_button(icon_name, tooltip, flat=True, valign=Gtk.Align.CENTER,
                css=()):
    """A flat icon button with *tooltip* as its accessible label.

    *css* adds classes beyond the flat one, for the shapes this is
    otherwise re-created for: 'circular' for the round accept/dismiss
    buttons on a request, for instance. *valign* is here because the same
    button sits centred in a header bar and stretched in a row.

    It takes no handler: the caller connects ``clicked`` with
    :func:`connect_weak`, since a closure over the owner that the owner's
    own button holds is the cycle through C that helper prevents.
    """
    button = Gtk.Button(icon_name=icon_name, valign=valign)
    if flat:
        button.add_css_class('flat')
    for name in css:
        button.add_css_class(name)
    set_accessible_label(button, tooltip)
    return button
