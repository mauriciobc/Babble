# SPDX-License-Identifier: GPL-3.0-or-later

"""Telling the reader what happened: toasts, errors, confirmations,
screen-reader announcements, and the dialog base that keeps a toast
visible above a dialog."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

from . import appsettings, pagenav  # noqa: E402
from .mastodon import summarize_error  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402


def announce(widget, text,
             priority=Gtk.AccessibleAnnouncementPriority.MEDIUM):
    """Have screen readers speak *text* for *widget*, for news that has
    no focus change or toast of its own (toasts announce themselves);
    nothing when *widget* is None."""
    if widget is not None:
        widget.announce(text, priority)


def show_toast(widget, text, undo=None, undo_label=None, dismissed=None):
    """Toast on the nearest Adw.ToastOverlay holding *widget*, else on the
    Adw.PreferencesDialog holding it (or that it is), else on the window;
    *undo* adds a button calling it, *dismissed* runs when the toast goes
    away however it does. Returns the toast, or None when there was no
    window to show it on. *text* is plain text."""
    window = pagenav.window_of(widget)
    if window is None:
        return None
    toast = Adw.Toast(title=text, use_markup=False)
    if dismissed is not None:
        toast.connect('dismissed', lambda _toast: dismissed())
    if undo is not None:
        toast.set_button_label(undo_label or _('_Undo'))
        toast.set_priority(Adw.ToastPriority.HIGH)
        toast.connect('button-clicked', lambda _toast: undo())
    # A dialog presented over the window covers the window's overlay, so
    # prefer the overlay inside the dialog the widget lives in.
    overlay = widget.get_ancestor(Adw.ToastOverlay) if widget is not None \
        else None
    if overlay is not None and overlay is not window.toast_overlay:
        overlay.add_toast(toast)
        return toast
    # Adw.PreferencesDialog keeps its overlay inside itself, so it is not an
    # ancestor of the dialog (or of a dialog presented over it); it offers
    # add_toast instead. get_ancestor counts the widget itself.
    preferences = widget.get_ancestor(Adw.PreferencesDialog) \
        if widget is not None else None
    if preferences is not None:
        preferences.add_toast(toast)
    else:
        window.add_toast(toast)
    return toast


# Actions held back until their Undo toast is gone. Each entry is the
# function that commits one, so the window can flush them when it closes.
_pending_actions = []


def deferred_action(widget, text, commit, restore, undo_label=None):
    """Offer Undo before a destructive request: show *text* with an Undo
    button, call *restore()* if it is pressed and *commit()* once the toast
    goes away without that. Exactly one of them runs, once. With no window
    to show the toast on, *commit* runs at once. :func:`flush_deferred`
    commits whatever is still waiting."""
    state = {'settled': False}

    def settle(undone):
        if state['settled']:
            return
        state['settled'] = True
        if commit_pending in _pending_actions:
            _pending_actions.remove(commit_pending)
        (restore if undone else commit)()

    def commit_pending():
        settle(False)

    _pending_actions.append(commit_pending)
    toast = show_toast(widget, text, undo=lambda: settle(True),
                       undo_label=undo_label,
                       dismissed=lambda: settle(False))
    if toast is None:
        settle(False)


def flush_deferred():
    """Commit every action still waiting on its Undo toast, for a window
    that is closing: the reader chose it, so it must not be dropped."""
    for commit_pending in list(_pending_actions):
        commit_pending()


def report_error(widget, heading, error):
    """Toast '<heading>: <server message>' for a GLib.Error. A cancelled
    operation is the reader's own doing, so it stays silent."""
    if (isinstance(error, GLib.Error)
            and error.matches(Gio.io_error_quark(),
                              Gio.IOErrorEnum.CANCELLED)):
        return
    message = getattr(error, 'message', None) or str(error)
    show_toast(widget, '%s: %s' % (heading, summarize_error(message)))


def confirm(widget, heading, body, action_label, callback,
            destructive=True):
    """Adw.AlertDialog with Cancel and *action_label*; *callback()* runs
    only when the action is chosen."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('ok', action_label)
    if destructive:
        dialog.set_response_appearance('ok',
                                       Adw.ResponseAppearance.DESTRUCTIVE)
    else:
        dialog.set_response_appearance('ok', Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')

    def on_response(_dialog, response):
        if response == 'ok':
            callback()

    dialog.connect('response', on_response)
    dialog.present(widget)
    return dialog


def confirm_remind(widget, heading, body, action, action_label, setting,
                   callback):
    """Cancel, "Don't Remind Me Again", and *action_label*.

    The reminder turns the boolean *setting* off and still runs
    *callback()*, the same as choosing the action. Cancel does neither.
    The alt-text boost reminder is not this dialog: it is a checkbox on
    the extra child, and its default is Cancel."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('never', _('_Don’t Remind Me Again'))
    dialog.add_response(action, action_label)
    dialog.set_response_appearance(action, Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response(action)
    dialog.set_close_response('cancel')

    def on_response(_dialog, response):
        if response == 'never':
            appsettings.settings().set_boolean(setting, False)
        if response in ('never', action):
            callback()

    dialog.connect('response', on_response)
    dialog.present(widget)
    return dialog


class ToastDialog(Adw.Dialog):
    """Dialog with a header bar, its own toast overlay and a page.

    A dialog covers the window's toast overlay, so a toast raised inside
    one would land behind it; every dialog either subclasses this or
    says why it cannot (``dialog toasts`` enforces the rule). Callers
    that need something other than a preferences page replace it with
    :meth:`set_content`.
    """

    def __init__(self, title, width=480, height=600, title_buttons=True,
                 **kwargs):
        super().__init__(title=title, content_width=width,
                         content_height=height, **kwargs)
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        if not title_buttons:
            self.header.set_show_start_title_buttons(False)
            self.header.set_show_end_title_buttons(False)
        self.toolbar.add_top_bar(self.header)
        self.toasts = Adw.ToastOverlay()
        self.page = Adw.PreferencesPage()
        self.toasts.set_child(self.page)
        self.toolbar.set_content(self.toasts)
        self.set_child(self.toolbar)

    def set_content(self, widget):
        """Replace the preferences page, keeping the overlay above it."""
        self.page = widget
        self.toasts.set_child(widget)

    def toast(self, text):
        self.toasts.add_toast(
            Adw.Toast(title=text, timeout=5, use_markup=False))

    def error(self, heading, error):
        self.toast('%s: %s' % (heading, summarize_error(error)))

    def submit_on_enter(self, button, *rows):
        """Make Enter in *rows* press *button*. The default widget alone
        does nothing for an entry row: the row has to opt in with
        ``activates-default``. An insensitive *button* is not pressed."""
        self.set_default_widget(button)
        for row in rows:
            row.set_activates_default(True)

    def add_header_buttons(self, cancel_label, action_label, callback,
                           css=None, sensitive=True):
        """Cancel button that closes the dialog plus an end-side action
        button calling *callback*; returns the action button."""
        cancel = Gtk.Button(label=cancel_label, use_underline=True)
        connect_weak(cancel, 'clicked', self._on_cancel)
        self.header.pack_start(cancel)
        button = Gtk.Button(label=action_label, use_underline=True,
                            sensitive=sensitive)
        if css:
            button.add_css_class(css)
        connect_weak(button, 'clicked', callback)
        self.header.pack_end(button)
        return button

    def _on_cancel(self, _button):
        self.force_close()
