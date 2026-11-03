# SPDX-License-Identifier: GPL-3.0-or-later

"""The web column as a plugin's panel: :class:`plugin.Panel` over the
column ``webcolumn`` builds.

Three rules shape it, because plugins are trusted code but the view is a
browser:

- Script is bound to the origin the plugin opened. It is checked before
  the script is sent, and again inside the page when it runs (a
  navigation can land in between), so a script never executes on another
  origin's document.
- Script runs in a WebKit world of its own, one per plugin, and the
  message handler exists only in that world: the page's own script can
  neither see nor call it.
- Nothing calls the plugin once the handle is closed or the plugin is
  disabled: a closed handle drops its callbacks and cancels the script
  still running.
"""

import json
from urllib.parse import urlsplit

from gi.repository import Gio, GLib

from . import plugin as api

MESSAGE_HANDLER = 'babble'


def origin_of(uri):
    """``scheme://host[:port]`` of an http(s) *uri*, else ''."""
    try:
        parts = urlsplit(uri or '')
        host, port = parts.hostname, parts.port
    except ValueError:
        return ''
    if parts.scheme.lower() not in ('http', 'https') or not host:
        return ''
    if ':' in host:
        host = '[%s]' % host
    origin = '%s://%s' % (parts.scheme.lower(), host)
    return origin if port is None else '%s:%d' % (origin, port)


def _plain(value):
    """A JavaScriptCore value as plain JSON data (None when it has none)."""
    if value is None or value.is_undefined():
        return None
    try:
        return json.loads(value.to_json(0))
    except (TypeError, ValueError):
        return None


class _Panel(api.Panel):

    def __init__(self, context, url):
        self._context = context
        self._open = True
        self._uri = url
        self._origin = origin_of(url)
        self._world = 'babble-plugin:' + context.plugin_id
        self._column_ref = None  # GObject weak ref: the column owns us
        self._handle = None
        self._message = None
        self._cancellable = Gio.Cancellable()

    # The contract

    @property
    def uri(self):
        column = self._column()
        return (column.view.get_uri() if column is not None else None) \
            or self._uri

    @property
    def origin(self):
        return self._origin

    @property
    def is_open(self):
        return self._open and self._context.active

    def load_uri(self, url):
        column = self._column()
        if not self.is_open or column is None:
            return
        origin = origin_of(url)
        if not origin:
            raise ValueError('only http and https URLs: %r' % url)
        self._uri, self._origin = url, origin
        column.load_uri(url, owner=self)

    def close(self):
        if not self._open:
            return
        column = self._column()
        if column is not None and column.panel is self:
            column.close_column()  # hides the slot; ends us on the way
        self._end()

    def run_script(self, script, callback=None):
        if not self.is_open:
            return
        if not isinstance(script, str):
            raise TypeError('script must be a string')
        column = self._column()
        error = self._refusal(column)
        if error is not None:
            if callback is not None:
                GLib.idle_add(self._deliver, callback, None, error)
            return
        # Checked in the page too: it may have moved on since the check
        # above, and the check there is the one that cannot be raced.
        guarded = 'if (location.origin !== %s) { throw new Error(%s); }\n%s' \
            % (json.dumps(self._origin), json.dumps('wrong origin'), script)

        def done(value, error):
            if callback is not None:
                self._deliver(callback, None if error else _plain(value),
                              error)
        column.evaluate(self._world, guarded, done, self._cancellable)

    def on_message(self, callback):
        self._message = callback

    # The host's

    def _column(self):
        return self._column_ref() if self._column_ref is not None else None

    def _attach(self, column):
        self._column_ref = column.weak_ref()
        self._handle = column.add_message_handler(
            self._world, MESSAGE_HANDLER, self._on_message)

    def _end(self):
        """Close the handle without touching the column's visibility."""
        if not self._open:
            return
        self._open = False
        self._message = None
        self._cancellable.cancel()
        handle, self._handle = self._handle, None
        column = self._column()
        if handle is not None:
            _remove_handle(handle)
        if column is not None:
            column.release_panel(self)

    def _refusal(self, column):
        if column is None:
            return 'the panel is closed'
        if origin_of(column.view.get_uri()) != self._origin:
            return 'the page is not on the origin the plugin opened'
        if not column.view.get_settings().get_enable_javascript():
            return 'JavaScript is turned off'
        return None

    def _deliver(self, callback, result, error):
        # Re-checked at delivery: it can be queued past a close.
        if self.is_open:
            self._context.guard(callback, result, error)
        return GLib.SOURCE_REMOVE

    def _on_message(self, value):
        callback = self._message
        column = self._column()
        if callback is None or column is None or not self.is_open \
                or origin_of(column.view.get_uri()) != self._origin:
            return
        self._context.guard(callback, _plain(value))


def _remove_handle(handle):
    from .webcolumn import WebColumn
    WebColumn.remove_message_handler(handle)


def open_panel(context, widget, url, title=None):
    """Show *url* in the column of *widget*'s window as a panel of
    *context*'s plugin. Without a column to show it in, the page opens in
    the browser and the panel comes back already closed."""
    if not origin_of(url):
        raise ValueError('only http and https URLs: %r' % url)
    from . import webcolumn
    panel = _Panel(context, url)
    if not context.active:
        panel._end()
        return panel
    column = webcolumn.present_panel(widget, url, panel, title)
    if column is None:
        panel._end()
        return panel
    panel._attach(column)
    return panel
