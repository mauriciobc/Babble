# SPDX-License-Identifier: GPL-3.0-or-later

"""XDG desktop portal plumbing: Background (running with the window closed,
and the status line shown for it) and GlobalShortcuts (quick post).

Nothing here knows about :class:`src.window.BabbleWindow`. The two entry
points that answer back into the window take a GObject weak reference to
it, so a pending request never keeps a closed window alive, and a window
that went away mid-flight is simply not called back.
"""

import secrets

import gi

gi.require_version('Gio', '2.0')

try:
    from .i18n import gettext as _  # noqa: E402
except ImportError:  # loaded as a top-level module by the standalone tests
    from i18n import gettext as _  # noqa: E402

from gi.repository import Gio, GLib  # noqa: E402

_PORTAL_NAME = 'org.freedesktop.portal.Desktop'
_PORTAL_PATH = '/org/freedesktop/portal/desktop'
BACKGROUND = 'org.freedesktop.portal.Background'
SHORTCUTS = 'org.freedesktop.portal.GlobalShortcuts'
_SESSION = 'org.freedesktop.portal.Session'

# The Background portal refuses status messages longer than this.
STATUS_MAX_CHARS = 96
# Status changes arriving within this many milliseconds share one call.
STATUS_DELAY_MS = 1000
QUICK_POST_SHORTCUT = 'quick-post'
# A trigger as the XDG shortcuts specification writes it (LOGO is Super).
QUICK_POST_TRIGGER = 'LOGO+SHIFT+m'

# Portal interface -> its version (0: missing), as first answered.
_versions = {}


def _swallow_call(connection, result):
    """Take delivery of a best-effort D-Bus reply, discarding failure."""
    try:
        connection.call_finish(result)
    except GLib.Error:
        pass


def session_bus(callback):
    """Hand *callback* the session bus, or None when there is none."""
    def done(_source, result):
        try:
            bus = Gio.bus_get_finish(result)
        except GLib.Error:
            bus = None
        callback(bus)
    Gio.bus_get(Gio.BusType.SESSION, None, done)


def portal_version(interface, callback):
    """Call *callback(version)* with the version of the portal
    *interface*, 0 when the portal or that interface is missing. The
    answer is cached; a missing session bus is not, it may come back."""
    if interface in _versions:
        callback(_versions[interface])
        return

    def on_got(bus, result):
        try:
            version, = bus.call_finish(result).unpack()
        except GLib.Error:
            version = 0
        _versions[interface] = int(version)
        callback(_versions[interface])

    def on_bus(bus):
        if bus is None:
            callback(0)
            return
        bus.call(_PORTAL_NAME, _PORTAL_PATH,
                 'org.freedesktop.DBus.Properties', 'Get',
                 GLib.Variant('(ss)', (interface, 'version')),
                 GLib.VariantType('(v)'), Gio.DBusCallFlags.NONE, -1, None,
                 on_got)
    session_bus(on_bus)


def portal_request(bus, interface, method, signature, args, options,
                   on_response):
    """Call the portal *method*, whose answer arrives later as the
    ``org.freedesktop.portal.Request`` ``Response`` signal. *options*,
    the trailing ``a{sv}`` of *signature*, gains the ``handle_token``.
    *on_response(code, results)* runs once; *code* is None when the call
    itself failed (no portal, D-Bus error)."""
    token = 'babble_%s' % secrets.token_hex(8)
    sender = (bus.get_unique_name() or '').lstrip(':').replace('.', '_')
    request_path = '/org/freedesktop/portal/desktop/request/%s/%s' % (
        sender, token)
    state = {'id': 0}

    def on_signal(_connection, _sender, _path, _iface, _signal, params):
        unsubscribe()
        code, results = params.unpack()
        on_response(code, results)

    def subscribe(path):
        state['id'] = bus.signal_subscribe(
            _PORTAL_NAME, 'org.freedesktop.portal.Request', 'Response',
            path, None, Gio.DBusSignalFlags.NONE, on_signal)

    def unsubscribe():
        if state['id']:
            bus.signal_unsubscribe(state['id'])
            state['id'] = 0

    # Subscribe before calling so the response cannot slip past.
    subscribe(request_path)

    def on_called(connection, call_result):
        try:
            handle, = connection.call_finish(call_result).unpack()
        except GLib.Error:
            unsubscribe()
            on_response(None, {})
            return
        if handle != request_path and state['id']:
            # Pre-0.9 portals pick their own request path.
            unsubscribe()
            subscribe(handle)

    options = dict(options, handle_token=GLib.Variant('s', token))
    bus.call(_PORTAL_NAME, _PORTAL_PATH, interface, method,
             GLib.Variant(signature, (*args, options)),
             GLib.VariantType('(o)'), Gio.DBusCallFlags.NONE, -1, None,
             on_called)


def request_background(window_ref, bus):
    """Ask Background for permission to run with the window closed, and
    call the window's ``_background_done(allowed)`` or
    ``_background_failed()`` with the answer."""
    if bus is None:
        _background_finish(window_ref, None)
        return
    if window_ref() is None:
        return

    def on_response(code, results):
        _background_finish(window_ref, None if code is None else
                           code == 0 and bool(results.get('background')))

    portal_request(bus, BACKGROUND, 'RequestBackground', '(sa{sv})',
                   ('',), {
                       'reason': GLib.Variant('s', _(
                           'Deliver notifications while the window is '
                           'closed')),
                       'autostart': GLib.Variant('b', False),
                   }, on_response)


def _background_finish(window_ref, allowed):
    window = window_ref()
    if window is None:
        return
    if allowed is None:  # no portal: nothing to ask, nothing to undo
        window._background_failed()
    else:
        window._background_done(allowed)


def set_background_status(message):
    """Best-effort ``Background.SetStatus``, which needs version 2 of
    the portal; *message* '' clears the status."""
    def on_bus(bus):
        if bus is not None:
            bus.call(_PORTAL_NAME, _PORTAL_PATH, BACKGROUND, 'SetStatus',
                     GLib.Variant('(a{sv})', (
                         {'message': GLib.Variant('s', message)},)),
                     None, Gio.DBusCallFlags.NONE, -1, None, _swallow_call)

    def on_version(version):
        if version >= 2:
            session_bus(on_bus)

    portal_version(BACKGROUND, on_version)


class _ShortcutSession:
    """A GlobalShortcuts session with the quick post shortcut bound.
    Holds no window: activating the shortcut goes through the
    application's ``compose`` action."""

    def __init__(self, bus, handle, on_lost):
        self.bus = bus
        self.handle = handle
        self._on_lost = on_lost
        self._subscriptions = [
            bus.signal_subscribe(_PORTAL_NAME, SHORTCUTS, 'Activated',
                                 _PORTAL_PATH, None,
                                 Gio.DBusSignalFlags.NONE,
                                 self._on_activated),
            bus.signal_subscribe(_PORTAL_NAME, _SESSION, 'Closed', handle,
                                 None, Gio.DBusSignalFlags.NONE,
                                 self._on_closed),
        ]

    def _on_activated(self, _connection, _sender, _path, _iface, _signal,
                      params):
        session, shortcut_id = params.unpack()[:2]
        if session != self.handle or shortcut_id != QUICK_POST_SHORTCUT:
            return
        app = Gio.Application.get_default()
        if app is not None and app.has_action('compose'):
            app.activate_action('compose', None)

    def _on_closed(self, *_args):
        # Ended by the portal (or the user, in the system settings).
        self._unsubscribe()
        on_lost, self._on_lost = self._on_lost, None
        if on_lost is not None:
            on_lost(self)

    def _unsubscribe(self):
        for subscription in self._subscriptions:
            self.bus.signal_unsubscribe(subscription)
        self._subscriptions = []

    def close(self):
        if not self._subscriptions:
            return
        self._unsubscribe()
        self._on_lost = None
        self.bus.call(_PORTAL_NAME, self.handle, _SESSION, 'Close', None,
                      None, Gio.DBusCallFlags.NONE, -1, None, _swallow_call)


def _shortcut_finish(window_ref, session, refused=False):
    window = window_ref()
    if window is None:
        if session is not None:
            session.close()
        return
    window._shortcut_ready(session, refused)


def _shortcut_lost(window_ref, session):
    window = window_ref()
    if window is not None and window._shortcut_session is session:
        window._shortcut_session = None


def create_shortcut_session(window_ref, bus):
    """CreateSession, then BindShortcuts on the session it answers, then
    the window's ``_shortcut_ready(session, refused)``."""
    if bus is None:
        _shortcut_finish(window_ref, None)
        return

    def on_created(code, results):
        handle = results.get('session_handle') if code == 0 else None
        if not handle:
            _shortcut_finish(window_ref, None)
            return
        session = _ShortcutSession(
            bus, handle, lambda lost: _shortcut_lost(window_ref, lost))
        if window_ref() is None:
            session.close()
            return

        def on_bound(code, _results):
            if code == 0:
                _shortcut_finish(window_ref, session)
                return
            session.close()
            # 1 is the user saying no; 2 or a failed call refused nothing.
            _shortcut_finish(window_ref, None, refused=code == 1)

        shortcut = (QUICK_POST_SHORTCUT, {
            'description': GLib.Variant('s', _('New Post')),
            'preferred_trigger': GLib.Variant('s', QUICK_POST_TRIGGER),
        })
        portal_request(bus, SHORTCUTS, 'BindShortcuts',
                       '(oa(sa{sv})sa{sv})', (handle, [shortcut], ''), {},
                       on_bound)

    portal_request(bus, SHORTCUTS, 'CreateSession', '(a{sv})', (), {
        'session_handle_token': GLib.Variant(
            's', 'babble_%s' % secrets.token_hex(8)),
    }, on_created)
