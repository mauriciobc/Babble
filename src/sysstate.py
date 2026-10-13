# SPDX-License-Identifier: GPL-3.0-or-later

"""What the system says about the network and power, in one place.

:func:`monitor` hands out a process-wide :class:`SystemState` whose
read-only boolean properties ``online``, ``metered`` and ``power-saver``
follow ``Gio.NetworkMonitor`` and ``Gio.PowerProfileMonitor`` (both go
through the portals inside a sandbox); ``notify::<name>`` fires when one
changes. The network monitor reports a change as a burst of signals (an
interface going down, routes being rewritten, the connectivity check
coming back), so they are coalesced over :data:`SETTLE_MS` before the
properties are read again. ``network-changed`` fires once per settled
burst, even when no property moved, for callers that care about a new
route under an unchanged ``online`` (a Wi-Fi switch, a resume).

Consumers connect with ``pages.connect_weak``: the monitor lives for the
whole process and must not keep a page or a window alive.
"""

from gi.repository import Gio, GLib, GObject

# How long a burst of network signals is allowed to settle, in ms.
SETTLE_MS = 500

_ONLINE = (Gio.NetworkConnectivity.FULL, Gio.NetworkConnectivity.LIMITED)

_monitor = None


def monitor():
    """The process-wide :class:`SystemState`."""
    global _monitor
    if _monitor is None:
        _monitor = SystemState()
    return _monitor


class SystemState(GObject.Object):
    __gtype_name__ = 'BabbleSystemState'
    __gsignals__ = {
        'network-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._network = Gio.NetworkMonitor.get_default()
        self._power = Gio.PowerProfileMonitor.dup_default()
        self._online = self._read_online()
        self._metered = self._network.get_network_metered()
        self._power_saver = self._power.get_power_saver_enabled()
        self._settle_source = 0
        for signal in ('network-changed', 'notify::connectivity',
                       'notify::network-metered',
                       'notify::network-available'):
            self._network.connect(signal, self._on_network_signal)
        self._power.connect('notify::power-saver-enabled',
                            self._on_power_changed)

    @GObject.Property(type=bool, default=True,
                      flags=GObject.ParamFlags.READABLE)
    def online(self):
        return self._online

    @GObject.Property(type=bool, default=False,
                      flags=GObject.ParamFlags.READABLE)
    def metered(self):
        return self._metered

    @GObject.Property(type=bool, default=False,
                      flags=GObject.ParamFlags.READABLE)
    def power_saver(self):
        return self._power_saver

    def _read_online(self):
        return self._network.get_connectivity() in _ONLINE

    def _on_network_signal(self, *_args):
        # A fixed window from the first signal, not one restarted by
        # every signal: a link that keeps flapping still gets read.
        if not self._settle_source:
            self._settle_source = GLib.timeout_add(SETTLE_MS, self._settle)

    def _settle(self):
        self._settle_source = 0
        online = self._read_online()
        metered = self._network.get_network_metered()
        if online != self._online:
            self._online = online
            self.notify('online')
        if metered != self._metered:
            self._metered = metered
            self.notify('metered')
        self.emit('network-changed')
        return GLib.SOURCE_REMOVE

    def _on_power_changed(self, *_args):
        enabled = self._power.get_power_saver_enabled()
        if enabled != self._power_saver:
            self._power_saver = enabled
            self.notify('power-saver')
