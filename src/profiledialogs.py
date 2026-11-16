# SPDX-License-Identifier: GPL-3.0-or-later
"""The profile's own dialogs: mute duration and the private note.

Both are opened from the profile page or its action menu and both write
through ``_request_result``, so they live together rather than with the
page that presents them.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import feedback  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402



# Seconds, as Mastodon's ``duration`` parameter wants them.
# The api_versions.mastodon value that introduced the ``endorse`` and
# ``unendorse`` aliases. Before it the route only answered ``pin`` and
# ``unpin``. See set_endorsed, which falls back on a 404 as well.
MUTE_DURATIONS = (0, 3600, 6 * 3600, 24 * 3600, 7 * 24 * 3600,
                  30 * 24 * 3600)

NOTE_LIMIT = 2000

def duration_label(seconds):
    if not seconds:
        return _('Never')
    if seconds < 24 * 3600:
        hours = seconds // 3600
        return ngettext('%d Hour', '%d Hours', hours) % hours
    days = seconds // (24 * 3600)
    return ngettext('%d Day', '%d Days', days) % days

def present_mute_dialog(parent, handle, on_confirm):
    """Mute confirmation with an expiry and the notifications switch;
    *on_confirm(duration_seconds, hide_notifications)* runs on Mute."""
    dialog = Adw.AlertDialog(
        heading=_('Mute %s?') % handle,
        body=_('Their posts will be hidden. They will not be told that '
               'you muted them.'))
    durations = Gtk.StringList.new([duration_label(s)
                                    for s in MUTE_DURATIONS])
    expire_row = Adw.ComboRow(title=_('Expire In'), model=durations)
    notifications_row = Adw.SwitchRow(title=_('Hide Notifications'),
                                      active=True)
    group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    group.add_css_class('boxed-list')
    group.append(expire_row)
    group.append(notifications_row)
    dialog.set_extra_child(group)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('mute', _('_Mute'))
    dialog.set_response_appearance('mute',
                                   Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response('cancel')
    dialog.set_close_response('cancel')

    def on_response(_dialog, response):
        if response == 'mute':
            on_confirm(MUTE_DURATIONS[expire_row.get_selected()],
                       notifications_row.get_active())

    dialog.connect('response', on_response)
    dialog.present(parent)
    return dialog

class PrivateNoteDialog(feedback.ToastDialog):
    """Editor for the private note kept on another account (visible to
    the viewer only). *on_save(text)* is called with the new text; the
    caller closes the dialog by calling :meth:`close` on success."""

    __gtype_name__ = 'BabblePrivateNoteDialog'

    def __init__(self, handle, text, on_save, **kwargs):
        super().__init__(_('Private Note'), 400, 360,
                         title_buttons=False, **kwargs)
        self._on_save = on_save
        header = self.header
        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        connect_weak(cancel, 'clicked', self._on_cancel_clicked)
        header.pack_start(cancel)
        self.save_button = Gtk.Button(label=_('_Save'), use_underline=True)
        self.save_button.add_css_class('suggested-action')
        connect_weak(self.save_button, 'clicked', self._on_save_clicked)
        header.pack_end(self.save_button)
        self.counter = Gtk.Label(label=str(NOTE_LIMIT),
                                 tooltip_text=_('Remaining Characters'))
        self.counter.add_css_class('numeric')
        self.counter.add_css_class('dimmed')
        header.pack_end(self.counter)

        self.text_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8,
            vexpand=True, hexpand=True)
        self.text_view.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Private note about %s') % handle])
        self.buffer = self.text_view.get_buffer()
        self.buffer.set_text(text or '', -1)
        connect_weak(self.buffer, 'changed', self._on_changed)
        scrolled = Gtk.ScrolledWindow(child=self.text_view, vexpand=True,
                                      margin_start=12, margin_end=12,
                                      margin_top=12, margin_bottom=12)
        scrolled.add_css_class('card')
        hint = Gtk.Label(
            label=_('Only you can see this note about %s.') % handle,
            xalign=0.0, wrap=True, margin_start=12, margin_end=12,
            margin_bottom=12)
        hint.add_css_class('dimmed')
        hint.add_css_class('caption')
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(scrolled)
        body.append(hint)
        self.set_content(body)
        self.set_focus(self.text_view)
        self._on_changed()

    def text(self):
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, False)

    def _on_changed(self, *_args):
        remaining = NOTE_LIMIT - self.buffer.get_char_count()
        self.counter.set_label(str(remaining))
        if remaining < 0:
            self.counter.add_css_class('error')
        else:
            self.counter.remove_css_class('error')
        self.save_button.set_sensitive(remaining >= 0)

    def _on_cancel_clicked(self, _button):
        self.close()

    def _on_save_clicked(self, _button):
        self.save_button.set_sensitive(False)
        self._on_save(self.text())

def _request_result(client, result, finish, widget=None, what=None):
    """Decoded body of *result*, or None when the request failed.

    *finish* is the api module's own ``<name>_finish`` of the request.

    Used for the extras that decorate a profile -- familiar followers,
    featured hashtags, endorsements -- and for reloading the account
    itself. A failure there used to vanish completely: no toast, no log,
    no sign the card was short of something it should have shown. When
    the caller names *widget* and *what*, the failure is reported, the
    same way a relationship failure is.
    """
    try:
        return finish(client, result)
    except GLib.Error as error:
        if widget is not None and what is not None:
            feedback.report_error(widget, what, error)
        else:
            # Nothing to report it on: still leave a trace, or the next
            # person to wonder why a card is missing a section cannot.
            GLib.log_variant('Babble', GLib.LogLevelFlags.LEVEL_DEBUG,
                             GLib.Variant('a{sv}', {'MESSAGE': GLib.Variant(
                                 's', 'profile: request failed: %s'
                                 % error.message)}))
        return None
