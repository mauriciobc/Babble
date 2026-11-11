# SPDX-License-Identifier: GPL-3.0-or-later
"""Media the user's own server does not hold.

A Mastodon attachment has ``url``, the copy the user's server cached, and
``remote_url``, the original on the author's server. Fetching the second
tells a server the user did not choose their IP address, client and
viewing time, so Babble goes through one gate first: the
``remote-media`` setting (``ask``, ``load`` or ``never``), and for
``ask`` a dialog. A cached ``url`` never asks. The decision (allow, ask or
deny) is :func:`mediapolicy.remote_access`; this module owns the dialog
and writes the choice back when the reader asks to remember it.

Only fetches go through the gate. Opening the original in the browser,
copying a URL and dragging one out do not fetch anything here.
"""

import functools
import urllib.parse

import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')

from .i18n import gettext as _  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import mediapolicy  # noqa: E402
from . import pages  # noqa: E402


def media_url(attachment):
    """``(url, is_remote)``: the copy on the user's server if there is
    one, else the original with ``is_remote`` set, else ``(None, False)``."""
    url = attachment.get('url')
    if url:
        return url, False
    url = attachment.get('remote_url')
    if url:
        return url, True
    return None, False


def host_of(url):
    return urllib.parse.urlsplit(url or '').hostname or ''


def request(widget, url, is_remote, on_allowed, on_denied):
    """Run *on_allowed()* if *url* may be fetched, else *on_denied(by_setting)*,
    where *by_setting* is True when the ``never`` setting refused it with
    no question asked. A URL on the user's server (``is_remote`` false) is
    allowed at once. Returns the dialog when one was shown, else None.

    The callbacks should be weak (:func:`pages.weak_method`) when they
    belong to a widget: the dialog outlives a call, not the widget."""
    if not is_remote:
        on_allowed()
        return None
    decision = mediapolicy.remote_access(mediapolicy.read_state(), True)
    if decision == mediapolicy.ALLOW:
        on_allowed()
        return None
    if decision == mediapolicy.DENY:
        on_denied(True)
        return None
    dialog = Adw.AlertDialog(
        heading=_('Load Media From Another Server?'),
        # Translators: {host} is the address of the server that holds the
        # original, such as example.social.
        body=_('Your server has no copy of this media. Loading it from '
               '{host} lets that server see your IP address and that '
               'you viewed it.').format(
                   host=host_of(url) or _('another server')))
    remember = Gtk.CheckButton(label=_('_Remember This Choice'),
                               use_underline=True, halign=Gtk.Align.CENTER)
    dialog.set_extra_child(remember)
    dialog.add_response('cancel', _('_Cancel'))
    dialog.add_response('load', _('_Load'))
    dialog.set_response_appearance('load', Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response('load')
    dialog.set_close_response('cancel')
    dialog.connect('response', _on_response, remember, on_allowed, on_denied)
    if widget.get_root() is None:
        # A viewer asks while it is being built, before it is in the
        # window: present once it is.
        GLib.idle_add(_present_late, dialog, widget, on_denied)
    else:
        dialog.present(widget)
    return dialog


def _present_late(dialog, widget, on_denied):
    if widget.get_root() is not None:
        dialog.present(widget)
    else:
        on_denied(False)
    return GLib.SOURCE_REMOVE


def _on_response(_dialog, response, remember, on_allowed, on_denied):
    allowed = response == 'load'
    remembered = remember.get_active()
    if remembered:
        pages.settings().set_string('remote-media',
                                    'load' if allowed else 'never')
    if allowed:
        on_allowed()
    else:
        # A remembered Cancel is now the setting: say where to change it.
        on_denied(remembered)


class RemoteGate:
    """Mixin of the media viewer: asks before a remote fetch, then runs
    the action named in ``_remote_actions`` with ``(attachment, url)``.

    Nothing here refers to the dialog, and the callbacks hold the viewer
    weakly, so a viewer closed under a question is freed."""

    # action name -> method name
    _remote_actions = {}

    def _gate_remote(self, action, attachment, url, is_remote):
        if is_remote and url in self._remote_allowed:
            is_remote = False
        if action == 'show':
            self._remote_token += 1
        token = self._remote_token
        if is_remote and action == 'show':
            self._show_loading()
        request(
            self, url, is_remote,
            functools.partial(
                pages.weak_method(self._on_remote_allowed),
                token, action, attachment, url, is_remote),
            functools.partial(
                pages.weak_method(self._on_remote_denied),
                token, action, attachment))

    def _on_remote_allowed(self, token, action, attachment, url, is_remote):
        if self._closed or (action == 'show'
                            and token != self._remote_token):
            return
        if is_remote:
            self._remote_allowed.add(url)
        getattr(self, self._remote_actions[action])(attachment, url)

    def _on_remote_denied(self, token, action, attachment, by_setting):
        if self._closed:
            return
        if action != 'show':
            if by_setting:
                self._notify(_('Loading media from other servers is '
                               'turned off'))
            return
        if token != self._remote_token:
            return
        description = _('Your server has no copy of this media. Open it '
                        'in the browser to view it on the original server.')
        if by_setting:
            description += ' ' + _(
                'To change this, set Media From Other Servers in '
                'Preferences.')
        self._show_error(_('Media Not Stored by Your Server'), description,
                         retry=True, browser=True)
