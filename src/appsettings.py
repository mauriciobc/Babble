# SPDX-License-Identifier: GPL-3.0-or-later

"""The application's one ``Gio.Settings``, shared by every module."""

import gi

gi.require_version('Gio', '2.0')

from gi.repository import Gio  # noqa: E402

SCHEMA_ID = 'io.github.mauriciobc.Babble'

_settings = None


def settings():
    """The application's Gio.Settings, shared by every module."""
    global _settings
    if _settings is None:
        _settings = Gio.Settings.new(SCHEMA_ID)
    return _settings
