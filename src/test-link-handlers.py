#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""linkhandlers: the claimers and the web opener link routing asks for,
and that importing webcolumn registers the real ones. No display, no
network, and it never skips."""

import os
# Never the user's real dconf.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import functools
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import linkhandlers  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


calls = []


def decline(widget, uri):
    calls.append(('decline', uri))
    return False


def take(widget, uri):
    calls.append(('take', uri))
    return True


def never_asked(widget, uri):
    calls.append(('never', uri))
    return True


check('nothing registered claims nothing',
      linkhandlers.claim(None, 'https://example.org/') is False)
check('nothing registered opens nothing in the app',
      linkhandlers.open_in_app(None, 'https://example.org/') is False)

linkhandlers.add_claimer(decline)
linkhandlers.add_claimer(decline)
check('a claimer that declines leaves the link alone',
      linkhandlers.claim(None, 'https://a/') is False
      and calls == [('decline', 'https://a/')],
      repr(calls))

del calls[:]
linkhandlers.add_claimer(take)
linkhandlers.add_claimer(never_asked)
check('claimers are asked in registration order, the first yes wins',
      linkhandlers.claim(None, 'https://b/') is True
      and calls == [('decline', 'https://b/'), ('take', 'https://b/')],
      repr(calls))


class Owner:
    def method(self, widget, uri):
        return True


def nested():
    def inner(widget, uri):
        return True
    return inner


def with_default(widget, uri, extra=1):
    return True


@functools.wraps(take)
def wrapped(widget, uri):
    return True


bad = {
    'a lambda': lambda widget, uri: True,
    'a bound method': Owner().method,
    'a partial': functools.partial(take, None),
    'a nested function': nested(),
    'a function with a default': with_default,
    'a functools.wraps wrapper': wrapped,
    'a closure': (lambda x: (lambda widget, uri: x))(1),
}
for what, handler in bad.items():
    for register in (linkhandlers.add_claimer, linkhandlers.set_web_opener):
        try:
            register(handler)
        except TypeError:
            check('%s refuses %s' % (register.__name__, what), True)
        else:
            check('%s refuses %s' % (register.__name__, what), False,
                  'accepted')

del calls[:]
check('a refused claimer was not registered',
      linkhandlers.claim(None, 'https://c/') is True
      and calls == [('decline', 'https://c/'), ('take', 'https://c/')],
      repr(calls))


def opens(widget, uri):
    calls.append(('opens', uri))
    return True


def refuses(widget, uri):
    calls.append(('refuses', uri))
    return False


linkhandlers.set_web_opener(opens)
del calls[:]
check('the web opener shows the link', linkhandlers.open_in_app(
    None, 'https://d/') is True and calls == [('opens', 'https://d/')])
linkhandlers.set_web_opener(refuses)
check('an opener that declines is reported as False',
      linkhandlers.open_in_app(None, 'https://e/') is False)

# A handler that raises must not take link routing down: the claimer is
# skipped and logged, the next one still runs; an opener that raises is
# logged and counts as declining.
import logging  # noqa: E402


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


records = Records()
linkhandlers._log.addHandler(records)
linkhandlers._log.propagate = False  # keep the expected tracebacks quiet


def explode(widget, uri):
    calls.append(('explode', uri))
    raise RuntimeError('boom')


def explode_opener(widget, uri):
    raise RuntimeError('boom')


del linkhandlers._claimers[:]
linkhandlers.add_claimer(explode)
linkhandlers.add_claimer(take)
del calls[:]
try:
    claimed = linkhandlers.claim(None, 'https://f/')
except Exception as error:
    claimed = error
check('a raising claimer is skipped and the next claimer still runs',
      claimed is True and calls == [('explode', 'https://f/'),
                                    ('take', 'https://f/')],
      repr((claimed, calls)))
check('and the failure is logged with its traceback',
      len(records.records) == 1 and records.records[0].exc_info is not None,
      repr(records.records))
del records.records[:]
linkhandlers.set_web_opener(explode_opener)
try:
    opened = linkhandlers.open_in_app(None, 'https://g/')
except Exception as error:
    opened = error
check('a raising opener gives False, so the link goes to the browser',
      opened is False, repr(opened))
check('and that failure is logged too', len(records.records) == 1,
      repr(records.records))
linkhandlers._log.removeHandler(records)
linkhandlers._log.propagate = True

# The startup precondition: link routing finds a plugin's claimer and the
# web column's opener only because importing webcolumn (the window does at
# startup) registers them. Importing needs the libraries, not a display.
del linkhandlers._claimers[:]
linkhandlers._web_opener = None
from src import pluginlinks, webcolumn  # noqa: E402

check('importing webcolumn registers the plugin claimer',
      pluginlinks.claim_link in linkhandlers._claimers,
      repr(linkhandlers._claimers))
check('importing webcolumn registers the in-app opener',
      linkhandlers._web_opener is webcolumn._open_in_column,
      repr(linkhandlers._web_opener))

if failures:
    print('\n%d check(s) failed' % len(failures))
    sys.exit(1)
