# SPDX-License-Identifier: GPL-3.0-or-later

"""Small pieces of the plugin host that need nothing of it: a plugin's
settings file and the scope that owns what an extension connected."""

import json
import os

from gi.repository import GLib, GObject, Gio  # noqa: E402

from . import plugin as api  # noqa: E402


class PluginSettings(api.Settings):
    """A JSON file per plugin, under the user's config directory."""

    def __init__(self, plugin_id):
        self.path = os.path.join(GLib.get_user_config_dir(), 'babble',
                                 'plugins', plugin_id + '.json')
        self._values = None

    def _load(self):
        if self._values is None:
            try:
                with open(self.path, encoding='utf-8') as source:
                    values = json.load(source)
                self._values = values if isinstance(values, dict) else {}
            except (OSError, ValueError):
                self._values = {}
        return self._values

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        GLib.file_set_contents(self.path, json.dumps(
            self._values, ensure_ascii=False, indent=1).encode('utf-8'))

    def get(self, key, default=None):
        return self._load().get(key, default)

    def set(self, key, value):
        json.dumps(value)  # refuse what could not be saved, here
        self._load()[key] = value
        self._save()

    def remove(self, key):
        if self._load().pop(key, None) is not None:
            self._save()


class Scope:
    """What one owner made through the Context: the plugin itself, or
    one of its extensions. Closing it disconnects its handlers and
    cancels its requests, whose callbacks then never run."""

    def __init__(self):
        self.active = True
        self.handlers = []  # (GObject weak ref to the emitter, id)
        self.cancellable = Gio.Cancellable()

    def close(self):
        self.active = False
        self.cancellable.cancel()
        for ref, handler_id in self.handlers:
            emitter = ref()
            if emitter is not None and GObject.signal_handler_is_connected(
                    emitter, handler_id):
                emitter.disconnect(handler_id)
        self.handlers = []
