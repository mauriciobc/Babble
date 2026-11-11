# SPDX-License-Identifier: GPL-3.0-or-later

"""The media policy: may this media load, or play, right now?

This module is the only place that reads the media gates (the settings
keys, GTK's motion settings, the system's power and metering state) and the
only place that decides from them. Its callers are the media viewer
(:mod:`mediaplayback`), the inline GIFV player (:mod:`mediainline`), the
remote-media gate (:mod:`remotemedia`) and animated custom emoji
(:mod:`emojianim`); each asks it and acts on the answer.

* :class:`MediaState` is an immutable value holding everything an answer
  depends on, and :func:`read_state` builds it from GSettings, GTK's
  settings and the system monitor in one go.
* The decision functions (:func:`may_loop`, :func:`stream_deferral`,
  :func:`remote_access`, :func:`inline_may_play`) are pure: they take a
  state and return a value (never UI text), so they run with no display
  and no monitor, and the tests hand them explicit states.
* :func:`connect_changes` is the one list of signals that can change an
  answer, so a caller that follows the answers connects them all.

Nothing here builds a widget or a dialog: the viewer picks its reason
strings from the value returned, and ``remotemedia.request`` owns the
dialog and the Remember This Choice write. The system monitor
(:mod:`sysstate`) is built the first time a state is read, never at import.
"""

import collections
import types

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import GObject, Gtk  # noqa: E402

# Why a stream may not start by itself (see stream_deferral).
DEFER_NEVER = 'never'
DEFER_METERED = 'metered'

# What to do about a fetch of media (see remote_access).
ALLOW = 'allow'
ASK = 'ask'
DENY = 'deny'

# The GSettings keys whose change can alter an answer.
SETTINGS_KEYS = ('autoplay-animated-media', 'media-autoload',
                 'remote-media', 'inline-animated-media')
# The Gtk.Settings properties whose change can alter an answer.
GTK_PROPERTIES = ('gtk-enable-animations', 'gtk-interface-reduced-motion')
# The sysstate monitor properties whose change can alter an answer.
SYSTEM_PROPERTIES = ('power-saver', 'metered')


class MediaState(collections.namedtuple('MediaState', (
        'autoplay', 'autoload', 'remote', 'inline', 'animations',
        'reduced_motion', 'power_saver', 'metered'))):
    """Everything the media decisions depend on.

    ``autoplay`` is the ``autoplay-animated-media`` key, ``autoload`` the
    ``media-autoload`` mode ('always', 'unmetered', 'never'), ``remote``
    the ``remote-media`` mode ('ask', 'load', 'never'), ``inline`` the
    ``inline-animated-media`` key; ``animations`` is whether GTK has
    animations enabled and ``reduced_motion`` whether the user asked for
    reduced motion; ``power_saver`` and ``metered`` are the system's.
    The defaults are the schema's, on an ordinary system, so a test
    names only what it varies (``MediaState(metered=True)``)."""

    __slots__ = ()

    def __new__(cls, autoplay=True, autoload='unmetered', remote='ask',
                inline=False, animations=True, reduced_motion=False,
                power_saver=False, metered=False):
        return super().__new__(
            cls, autoplay, autoload, remote, inline, animations,
            reduced_motion, power_saver, metered)


def read_state(widget=None):
    """The current :class:`MediaState`. GTK's settings are those of
    *widget*'s display when one is given, else the default display's (and
    the defaults when there is no display at all)."""
    from . import pages, sysstate
    settings = pages.settings()
    if widget is not None:
        gtk_settings = Gtk.Settings.get_for_display(widget.get_display())
    else:
        gtk_settings = Gtk.Settings.get_default()
    animations, reduced = True, False
    if gtk_settings is not None:
        animations = bool(gtk_settings.get_property('gtk-enable-animations'))
        reduced = (gtk_settings.get_property('gtk-interface-reduced-motion')
                   == Gtk.ReducedMotion.REDUCE)
    monitor = sysstate.monitor()
    return MediaState(
        autoplay=settings.get_boolean('autoplay-animated-media'),
        autoload=settings.get_string('media-autoload'),
        remote=settings.get_string('remote-media'),
        inline=settings.get_boolean('inline-animated-media'),
        animations=animations,
        reduced_motion=reduced,
        power_saver=bool(monitor.get_property('power-saver')),
        metered=bool(monitor.get_property('metered')))


def may_loop(state):
    """Whether looping, muted media (a GIFV) may run by itself: the user
    has not turned off Autoplay Animated Media, animations are on, no
    reduced motion is asked for, and the system is not saving power."""
    return (state.autoplay and state.animations
            and not state.reduced_motion and not state.power_saver)


def stream_deferral(state):
    """Why video, audio and GIFVs wait for a click instead of loading at
    once: :data:`DEFER_NEVER` when the user chose never to autoload,
    :data:`DEFER_METERED` when the mode is 'unmetered' and the connection
    is metered, None when the stream may start. Mode 'always' never
    waits."""
    if state.autoload == 'never':
        return DEFER_NEVER
    if state.autoload == 'unmetered' and state.metered:
        return DEFER_METERED
    return None


def remote_access(state, is_remote):
    """What to do about fetching media: :data:`ALLOW`, :data:`ASK` (the
    caller shows its dialog) or :data:`DENY`. A copy on the user's own
    server (*is_remote* false) is always allowed; one on another server
    follows the ``remote-media`` mode ('load', 'ask', 'never')."""
    if not is_remote or state.remote == 'load':
        return ALLOW
    if state.remote == 'never':
        return DENY
    return ASK


def inline_may_play(state):
    """Whether a GIFV tile in the timeline may play: Play Animated GIFs
    in Timelines is on, the loop rule allows it and a stream may start.
    Unlike the viewer, the inline player asks this live: when the answer
    turns False (power saving, reduced motion, a metered connection under
    'unmetered') the playing tile stops, and it resumes when the answer
    turns True. The remote-media rule is per tile and is
    :func:`remote_access`."""
    return (state.inline and may_loop(state)
            and stream_deferral(state) is None)


def connect_changes(handler, widget=None):
    """Call *handler* whenever something that can change an answer
    changes: the four settings keys, GTK's animation settings (of
    *widget*'s display, else the default) and the system's power-saver and
    metered state. This is the one list of those signals.

    *handler* takes ``*args`` and should recompute the answer it needs,
    acting only on a change. Exactly two kinds are accepted, because the
    emitters (the settings object, the system monitor) live for the whole
    process, so whatever a handler references lives as long:

    * a bound method of a ``GObject.Object``, held weakly
      (:func:`pages.connect_weak`): a widget or player that connects its
      own method forms no cycle through C and is freed as before;
    * a module-level function, connected directly. A weak reference to a
      function would drop it at once.

    What is checked for a function: it is a ``types.FunctionType``; its
    ``__qualname__`` equals its ``__name__`` (no ``Class.`` prefix, no
    ``<locals>``) and it is not a lambda; it has no closure
    (``__closure__`` is None); it has no default argument values
    (``__defaults__`` and ``__kwdefaults__`` are None); and its
    ``__dict__`` is empty. The last matters because ``functools.wraps``
    copies the wrapped function's names onto a wrapper, so the names alone
    would let a decorator's closure through, and it also records
    ``__wrapped__`` in ``__dict__``. Anything else (a lambda, a
    ``functools.partial``, a bound method of a plain Python object, a
    nested function, a wrapper) raises :exc:`TypeError` before anything is
    connected.

    What is not checked, and stays the caller's responsibility: a function
    can still reach an object through the module globals it reads (a
    module-level variable that is later set to a widget) or through an
    attribute that something else sets on it after the check. This is a
    guard against the usual ways of pinning a widget, not a proof."""
    from . import pages, sysstate

    if isinstance(handler, types.MethodType) \
            and isinstance(handler.__self__, GObject.Object):
        weak = True
    elif isinstance(handler, types.FunctionType) \
            and handler.__qualname__ == handler.__name__ \
            and handler.__name__ != '<lambda>' \
            and handler.__closure__ is None \
            and handler.__defaults__ is None \
            and handler.__kwdefaults__ is None \
            and not handler.__dict__:
        weak = False
    else:
        raise TypeError(
            'connect_changes takes a bound method of a GObject or a '
            'module-level function, not %r' % (handler,))

    def connect(emitter, signal):
        if weak:
            pages.connect_weak(emitter, signal, handler)
        else:
            emitter.connect(signal, handler)

    monitor = sysstate.monitor()
    for name in SYSTEM_PROPERTIES:
        connect(monitor, 'notify::' + name)
    if widget is not None:
        gtk_settings = Gtk.Settings.get_for_display(widget.get_display())
    else:
        gtk_settings = Gtk.Settings.get_default()
    if gtk_settings is not None:
        for name in GTK_PROPERTIES:
            connect(gtk_settings, 'notify::' + name)
    settings = pages.settings()
    for key in SETTINGS_KEYS:
        connect(settings, 'changed::' + key)
