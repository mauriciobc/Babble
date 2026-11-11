#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The media policy's decisions (mediapolicy.py), over explicit states.

Needs no display and no system monitor: every decision is a pure function
of a :class:`MediaState`, so each truth table is walked in full. The real
reader and the signal list are exercised by the media settings, inline and
viewer tests, which install a fake monitor.
"""

import itertools
import os
# Never the user's real dconf (see the settings isolation rule).
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import mediapolicy  # noqa: E402
from src.mediapolicy import MediaState  # noqa: E402

failures = []


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

# stream_deferral: every (mode, metered) pair.
for mode, metered, expected in (
        ('always', True, None), ('always', False, None),
        ('unmetered', True, mediapolicy.DEFER_METERED),
        ('unmetered', False, None),
        ('never', True, mediapolicy.DEFER_NEVER),
        ('never', False, mediapolicy.DEFER_NEVER)):
    got = mediapolicy.stream_deferral(
        MediaState(autoload=mode, metered=metered))
    check('stream_deferral %s, %s is %s' % (
        mode, 'metered' if metered else 'unmetered', expected),
        got == expected, repr(got))
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

# The test can fail: the comparisons above are the ones that would catch a
# wrong rule, so run them against a deliberately wrong one (a policy that
# defers on 'always' when metered) and require that they disagree.
def wrong_deferral(state):
    if state.metered:
        return mediapolicy.DEFER_METERED
    return mediapolicy.stream_deferral(state)


disagreements = [
    (mode, metered)
    for mode, metered in itertools.product(AUTOLOADS, BOOLS)
    if wrong_deferral(MediaState(autoload=mode, metered=metered))
    != mediapolicy.stream_deferral(MediaState(autoload=mode,
                                              metered=metered))]
check('a wrong rule is detected by the table (metered rows differ)',
      disagreements == [('always', True), ('never', True)],
      repr(disagreements))
check('a wrong expectation is not equal to the real answer',
      mediapolicy.stream_deferral(MediaState(autoload='always',
                                             metered=True))
      != mediapolicy.DEFER_METERED)

if failures:
    print('%d check(s) failed' % len(failures))
    sys.exit(1)
