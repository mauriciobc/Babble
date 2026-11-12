#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""requestscope: generations, cancellation, and the one question a callback
asks. Real Gio.Cancellable and Gio.Task objects; no display, no network,
and it never skips."""

import os
# Never the user's real dconf.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import gc
import sys

from gi.repository import GLib, GObject, Gio  # noqa: E402

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.requestscope import (RequestScope, Ticket,  # noqa: E402
                              is_cancelled_error)

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


def cancelled_error():
    return GLib.Error.new_literal(
        Gio.io_error_quark(), 'Operation was cancelled',
        Gio.IOErrorEnum.CANCELLED)


def failed_error():
    return GLib.Error.new_literal(
        Gio.io_error_quark(), 'boom', Gio.IOErrorEnum.FAILED)


def spin():
    context = GLib.MainContext.default()
    while context.iteration(False):
        pass


def previous_generation_is_cancelled(scope_cls):
    """begin() cancels what the previous generation started, not its own."""
    scope = scope_cls()
    first = scope.begin()
    second = scope.begin()
    return (first.cancellable.is_cancelled(),
            second.cancellable.is_cancelled())


# begin supersedes and cancels the previous generation
old, new = previous_generation_is_cancelled(RequestScope)
check('begin cancels the previous generation', old)
check('begin does not cancel the new generation', not new)

scope = RequestScope()
first = scope.begin()
check('an answer of the current generation is wanted', first.wanted())
second = scope.begin()
check('an answer of an older generation is not wanted', not first.wanted())
check('the newest generation is wanted', second.wanted())
check('a success of an older generation is dropped too',
      not first.wanted(None))
check('a failure of an older generation is dropped',
      not first.wanted(failed_error()))
check('a failure of the current generation is wanted (it is shown)',
      second.wanted(failed_error()))

# A CANCELLED error is never wanted
check('is_cancelled_error recognises CANCELLED',
      is_cancelled_error(cancelled_error()))
check('is_cancelled_error ignores another error',
      not is_cancelled_error(failed_error()))
check('is_cancelled_error ignores a non-GLib value',
      not is_cancelled_error(ValueError('x')) and not is_cancelled_error(None))
check('CANCELLED from the current generation is not wanted',
      not second.wanted(cancelled_error()))
check('CANCELLED from an older generation is not wanted',
      not first.wanted(cancelled_error()))

# Several requests in one generation share its cancellable
scope = RequestScope()
a = scope.begin()
b = scope.start()
c = scope.start()
check('start stays in the current generation',
      a.wanted() and b.wanted() and c.wanted())
check('one generation, one cancellable',
      a.cancellable is b.cancellable is c.cancellable)
scope.begin()
check('a begin supersedes every request of the generation',
      not a.wanted() and not b.wanted() and not c.wanted())
check('a begin cancels every request of the generation',
      a.cancellable.is_cancelled() and c.cancellable.is_cancelled())

# start before any begin is valid
scope = RequestScope()
early = scope.start()
check('start before begin is wanted', early.wanted())
scope.begin()
check('and the first begin supersedes it',
      not early.wanted() and early.cancellable.is_cancelled())

# close
scope = RequestScope()
a = scope.begin()
b = scope.start()
check('open scope reports not closed', not scope.closed)
scope.close()
check('close marks the scope closed', scope.closed)
check('close cancels everything outstanding',
      a.cancellable.is_cancelled() and b.cancellable.is_cancelled())
check('close drops every later answer', not a.wanted() and not b.wanted())
check('close drops a failure too', not a.wanted(failed_error()))
scope.close()
check('closing twice is harmless', scope.closed)
late = scope.start()
check('start after close hands out a ticket that is never wanted',
      not late.wanted() and late.cancellable.is_cancelled())
late = scope.begin()
check('begin after close does the same, and does not raise',
      not late.wanted() and late.cancellable.is_cancelled())


# Real tasks: a cancelled request still calls back, and finishing it raises
def run_task(scope_ticket, value, cancel_before_return=False,
             cancel_after_return=False, cancel_before_callback=False):
    """A real Gio.Task over the ticket's cancellable. Returns what the
    callback saw: (wanted, outcome)."""
    seen = []

    def callback(_source, result, _data=None):
        wanted = scope_ticket.wanted()
        try:
            outcome = ('value', result.propagate_boolean())
        except GLib.Error as error:
            outcome = ('error', error)
        seen.append((wanted, outcome))

    source = GObject.Object()
    task = Gio.Task.new(source, scope_ticket.cancellable, callback, None)
    if cancel_before_return:
        scope_ticket.cancellable.cancel()
    task.return_boolean(value)
    if cancel_after_return:
        scope_ticket.cancellable.cancel()
    spin()
    return seen


scope = RequestScope()
ticket = scope.begin()
seen = run_task(ticket, True)
check('a real answer of the current generation is wanted and decodes',
      seen == [(True, ('value', True))], repr(seen))

scope = RequestScope()
ticket = scope.begin()
# The real answer arrived, then the scope moved on before the callback ran.
seen = run_task(ticket, True, cancel_after_return=True)
check('real answer then cancel still calls the callback once',
      len(seen) == 1, repr(seen))
wanted, outcome = seen[0]
check('real answer then cancel is judged as dropped', not wanted)
check('and decoding it would have raised CANCELLED (so ask first)',
      outcome[0] == 'error' and is_cancelled_error(outcome[1]), repr(outcome))

scope = RequestScope()
ticket = scope.begin()
seen = run_task(ticket, True, cancel_before_return=True)
check('a request cancelled before its answer is dropped',
      len(seen) == 1 and not seen[0][0]
      and is_cancelled_error(seen[0][1][1]), repr(seen))

scope = RequestScope()
ticket = scope.begin()
scope.begin()
seen = run_task(ticket, True)
check('a superseded real task is dropped without raising into the caller',
      len(seen) == 1 and not seen[0][0], repr(seen))

scope = RequestScope()
ticket = scope.begin()
scope.close()
seen = run_task(ticket, True)
check('a task finished after close is dropped',
      len(seen) == 1 and not seen[0][0], repr(seen))

# No reference that keeps an owner alive
class Owner(GObject.Object):
    pass


owner = Owner()
owner.scope = RequestScope()
held = [owner.scope.begin(), owner.scope.start()]
weak = owner.weak_ref()
del owner
gc.collect()
check('a scope and its tickets do not keep their owner alive',
      weak() is None)
check('the scope holds only plain state',
      all(isinstance(value, (int, bool, Gio.Cancellable))
          for value in vars(held[0]._scope).values()))
check('a ticket holds its scope and a cancellable only',
      isinstance(held[0]._scope, RequestScope)
      and isinstance(held[0].cancellable, Gio.Cancellable))

# The checks above can fail: a scope that never cancels on begin is seen.
class SilentBegin(RequestScope):
    """begin() that forgets to cancel: the old cancellable is replaced
    without being cancelled."""

    def begin(self):
        self._generation += 1
        self._cancellable = Gio.Cancellable()
        return Ticket(self, self._generation, self._cancellable)


old, new = previous_generation_is_cancelled(SilentBegin)
check('the begin check bites: a scope that never cancels is detected',
      not old)

from src import requestscope  # noqa: E402

saved = requestscope.is_cancelled_error
requestscope.is_cancelled_error = lambda _error: False
broken = RequestScope().begin()
cancelled_is_wanted = broken.wanted(cancelled_error())
requestscope.is_cancelled_error = saved
check('the CANCELLED check bites: ignoring it makes the error wanted',
      cancelled_is_wanted)

if failures:
    print('\n%d check(s) failed' % len(failures))
    sys.exit(1)
