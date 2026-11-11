#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The media policy's decisions (mediapolicy.py), over explicit states.

Needs no display and no system monitor: every decision is a pure function
of a :class:`MediaState`, so each truth table is walked in full. The reader
is checked field by field against memory GSettings and a fake monitor, and
``connect_changes`` is checked for the handlers it refuses. What needs a
display (GTK's two settings in the reader, and every signal of
``connect_changes`` being delivered) is in ``test-media-settings.py``.
"""

import functools
import itertools
import os
# Never the user's real dconf (see the settings isolation rule).
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from gi.repository import GObject  # noqa: E402

from src import mediapolicy, pages, sysstate  # noqa: E402
from src.mediapolicy import MediaState  # noqa: E402

failures = []


class FakeMonitor(GObject.Object):
    """The system state as plain properties (no Gio monitor)."""
    __gsignals__ = {
        'network-changed': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    online = GObject.Property(type=bool, default=True)
    metered = GObject.Property(type=bool, default=False)
    power_saver = GObject.Property(type=bool, default=False)


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, ' (%s)' % detail if detail else ''))


BOOLS = (True, False)
AUTOLOADS = ('always', 'unmetered', 'never')
REMOTES = ('ask', 'load', 'never')

# The state is a value.
state = MediaState()
check('the defaults are the schema defaults on an ordinary system',
      state == MediaState(True, 'unmetered', 'ask', False, True, False,
                          False, False))
try:
    state.metered = True
    immutable = False
except AttributeError:
    immutable = True
check('assigning to a state field fails', immutable)
check('_replace names only what changes',
      state._replace(metered=True).metered is True
      and state._replace(metered=True).autoload == 'unmetered')

# may_loop: all sixteen combinations of the four conditions.
for autoplay, animations, reduced, saver in itertools.product(BOOLS, repeat=4):
    expected = autoplay and animations and not reduced and not saver
    got = mediapolicy.may_loop(MediaState(
        autoplay=autoplay, animations=animations, reduced_motion=reduced,
        power_saver=saver))
    check('may_loop autoplay=%s animations=%s reduced=%s saver=%s is %s'
          % (autoplay, animations, reduced, saver, expected),
          got is expected, repr(got))
check('may_loop ignores the autoload mode and the metered state',
      all(mediapolicy.may_loop(MediaState(autoload=mode, metered=metered))
          for mode in AUTOLOADS for metered in BOOLS))

# stream_deferral: every (mode, metered) pair. The table is judged by a
# function that returns the mismatches, so the same judge can be run
# against a deliberately wrong rule at the end.
DEFERRAL_TABLE = (
    ('always', True, None), ('always', False, None),
    ('unmetered', True, mediapolicy.DEFER_METERED),
    ('unmetered', False, None),
    ('never', True, mediapolicy.DEFER_NEVER),
    ('never', False, mediapolicy.DEFER_NEVER))


def deferral_mismatches(rule):
    """The rows of DEFERRAL_TABLE that *rule* (a function of a state)
    answers differently from the table: ``(mode, metered, expected, got)``."""
    found = []
    for mode, metered, expected in DEFERRAL_TABLE:
        got = rule(MediaState(autoload=mode, metered=metered))
        if got != expected:
            found.append((mode, metered, expected, got))
    return found


for mode, metered, expected in DEFERRAL_TABLE:
    check('stream_deferral %s, %s is %s' % (
        mode, 'metered' if metered else 'unmetered', expected),
        not [m for m in deferral_mismatches(mediapolicy.stream_deferral)
             if m[:2] == (mode, metered)])
check('stream_deferral matches every row of the table',
      deferral_mismatches(mediapolicy.stream_deferral) == [])
check('the two reasons differ',
      mediapolicy.DEFER_NEVER != mediapolicy.DEFER_METERED)
check('stream_deferral ignores motion and power settings',
      all(mediapolicy.stream_deferral(MediaState(
          autoplay=a, animations=b, reduced_motion=c, power_saver=d)) is None
          for a, b, c, d in itertools.product(BOOLS, repeat=4)))

# remote_access: every (mode, is_remote) pair.
for mode, is_remote, expected in (
        ('ask', False, mediapolicy.ALLOW), ('load', False, mediapolicy.ALLOW),
        ('never', False, mediapolicy.ALLOW),
        ('ask', True, mediapolicy.ASK), ('load', True, mediapolicy.ALLOW),
        ('never', True, mediapolicy.DENY)):
    got = mediapolicy.remote_access(MediaState(remote=mode), is_remote)
    check('remote_access %s, %s is %s' % (
        mode, 'another server' if is_remote else 'own server', expected),
        got == expected, repr(got))
check('the three remote answers differ',
      len({mediapolicy.ALLOW, mediapolicy.ASK, mediapolicy.DENY}) == 3)

# inline_may_play: the full table of the five conditions it depends on.
for inline, autoplay, animations, reduced, saver, mode, metered in \
        itertools.product(BOOLS, BOOLS, BOOLS, BOOLS, BOOLS, AUTOLOADS,
                          BOOLS):
    expected = (inline and autoplay and animations and not reduced
                and not saver and not (mode == 'never'
                                       or (mode == 'unmetered' and metered)))
    got = mediapolicy.inline_may_play(MediaState(
        autoplay=autoplay, autoload=mode, inline=inline,
        animations=animations, reduced_motion=reduced, power_saver=saver,
        metered=metered))
    if got is not expected:
        check('inline_may_play inline=%s autoplay=%s animations=%s '
              'reduced=%s saver=%s mode=%s metered=%s' % (
                  inline, autoplay, animations, reduced, saver, mode,
                  metered), False, repr(got))
        break
else:
    check('inline_may_play matches the table for all 192 combinations', True)
check('inline_may_play does not depend on the remote-media mode',
      all(mediapolicy.inline_may_play(MediaState(inline=True, remote=r))
          for r in REMOTES))
check('inline_may_play is on only with the inline switch',
      not mediapolicy.inline_may_play(MediaState(inline=False))
      and mediapolicy.inline_may_play(MediaState(inline=True)))

# The signals that invalidate the answers name every input of the state.
check('the settings keys cover the four keys',
      set(mediapolicy.SETTINGS_KEYS) == {
          'autoplay-animated-media', 'media-autoload', 'remote-media',
          'inline-animated-media'})
check('the GTK properties cover animations and reduced motion',
      set(mediapolicy.GTK_PROPERTIES) == {
          'gtk-enable-animations', 'gtk-interface-reduced-motion'})
check('the system properties cover power saving and metering',
      set(mediapolicy.SYSTEM_PROPERTIES) == {'power-saver', 'metered'})

# read_state, field by field: change one source at a time and require that
# exactly the matching field of the state changes. A swapped pair
# (power_saver and metered, autoplay and inline) fails here. Memory
# GSettings and a fake monitor need no display; the two GTK fields are
# checked in test-media-settings.py, which has one.
def changed_fields(before, after):
    return {name for name in MediaState._fields
            if getattr(before, name) != getattr(after, name)}


settings = pages.settings()
for key in mediapolicy.SETTINGS_KEYS:
    settings.reset(key)
monitor = FakeMonitor()
sysstate._monitor = monitor
base = mediapolicy.read_state()
for name, change, expected in (
        ('autoplay-animated-media',
         lambda: settings.set_boolean('autoplay-animated-media', False),
         {'autoplay'}),
        ('media-autoload',
         lambda: settings.set_string('media-autoload', 'never'),
         {'autoload'}),
        ('remote-media',
         lambda: settings.set_string('remote-media', 'never'),
         {'remote'}),
        ('inline-animated-media',
         lambda: settings.set_boolean('inline-animated-media', True),
         {'inline'}),
        ('power-saver',
         lambda: monitor.set_property('power-saver', True),
         {'power_saver'}),
        ('metered',
         lambda: monitor.set_property('metered', True),
         {'metered'})):
    change()
    got = changed_fields(base, mediapolicy.read_state())
    check('read_state: %s changes only %s' % (name, sorted(expected)),
          got == expected, repr(sorted(got)))
    for key in mediapolicy.SETTINGS_KEYS:
        settings.reset(key)
    monitor.set_property('power-saver', False)
    monitor.set_property('metered', False)
check('read_state is back to the base after the resets',
      mediapolicy.read_state() == base)
check('read_state reads the schema defaults',
      (base.autoplay, base.autoload, base.remote, base.inline,
       base.power_saver, base.metered)
      == (True, 'unmetered', 'ask', False, False, False))


# connect_changes accepts a bound GObject method and a module-level
# function, and refuses anything that could pin its owner through the
# process-wide emitters (nothing is connected when it raises).
class PlainObject:
    def method(self, *args):
        pass


def module_level_handler(*args):
    pass


def refused(handler):
    try:
        mediapolicy.connect_changes(handler)
    except TypeError:
        return True
    return False


def nested_handler():
    def inner(*args):
        pass
    return inner


check('connect_changes refuses a lambda', refused(lambda *a: None))
check('connect_changes refuses a functools.partial',
      refused(functools.partial(module_level_handler, 1)))
check('connect_changes refuses a bound method of a plain object',
      refused(PlainObject().method))
check('connect_changes refuses a nested function', refused(nested_handler()))
check('connect_changes refuses a builtin', refused(print))
check('connect_changes refuses a non-callable', refused(None))

# The test can fail: run the same judge that passes the real rule against a
# deliberately wrong one (it defers whenever the connection is metered,
# even on 'always', and reports 'metered' instead of 'never') and require
# that the judge reports exactly those rows. The expected failure is
# only asserted, never printed as FAIL or counted in the exit status.
def wrong_deferral(state):
    if state.metered:
        return mediapolicy.DEFER_METERED
    return mediapolicy.stream_deferral(state)


caught = deferral_mismatches(wrong_deferral)
check('the table reports a wrong rule (two rows fail)',
      [m[:2] for m in caught] == [('always', True), ('never', True)],
      repr(caught))

if failures:
    print('%d check(s) failed' % len(failures))
    sys.exit(1)
