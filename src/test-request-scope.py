#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""requestscope: generations, cancellation, and the callback that asks
whether an answer is still wanted before the handler sees it. Real
Gio.Cancellable and Gio.Task objects; no display, no network, and it never
skips."""

import os
# Never the user's real dconf.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import gc
import sys

from gi.repository import GLib, GObject, Gio  # noqa: E402

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.requestscope import RequestScope, Ticket  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


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

# A cancelled ticket is not wanted, whatever its generation
scope = RequestScope()
ticket = scope.begin()
ticket.cancellable.cancel()
check('a ticket whose cancellable is cancelled is not wanted, in the '
      'current generation', not ticket.wanted())

# Several requests in one generation share its cancellable
scope = RequestScope()
a = scope.begin()
b = scope.start()
c = scope.start()
check('start stays in the current generation',
      a.wanted() and b.wanted() and c.wanted())
check('one generation, one cancellable',
      a.cancellable is b.cancellable is c.cancellable)
check('start does not cancel what is out in the same generation',
      not a.cancellable.is_cancelled())
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
scope.close()
check('closing twice is harmless', scope.closed)
late = scope.start()
check('start after close hands out a ticket that is never wanted',
      not late.wanted() and late.cancellable.is_cancelled())
late = scope.begin()
check('begin after close does the same, and does not raise',
      not late.wanted() and late.cancellable.is_cancelled())

# The closed flag is its own reason: a ticket whose cancellable was never
# cancelled (made by hand, as no scope method does) is dropped once closed.
scope = RequestScope()
loose = Ticket(scope, scope._generation, Gio.Cancellable())
check('a loose ticket of an open scope is wanted', loose.wanted())
scope._closed = True    # closed without cancelling: only the flag speaks
check('closed alone, with a live cancellable, is not wanted',
      not loose.cancellable.is_cancelled() and not loose.wanted())


class Owner(GObject.Object):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.dropped = []

    def handle(self, client, result):
        self.calls.append((client, result))

    @staticmethod
    def on_drop(owner):
        owner.dropped.append(True)


# The ticket's callback: asks first, holds the owner weakly
owner = Owner()
scope = RequestScope()
ticket = scope.begin()
callback = ticket.callback(owner.handle)
callback('client', 'result')
check('a wanted answer reaches the handler as (client, result)',
      owner.calls == [('client', 'result')])
callback('client', 'again', None)
check('a third argument from the transport is ignored',
      owner.calls[-1] == ('client', 'again'))
scope.begin()
callback('client', 'stale')
check('a superseded answer never reaches the handler',
      len(owner.calls) == 2)
check('and no on_drop was asked for', owner.dropped == [])

owner = Owner()
scope = RequestScope()
ticket = scope.begin()
callback = ticket.callback(owner.handle, on_drop=Owner.on_drop)
callback('client', 'result')
check('on_drop is not called for a wanted answer',
      owner.dropped == [] and len(owner.calls) == 1)
scope.close()
callback('client', 'late')
check('on_drop is called with the owner for an unwanted answer, and the '
      'handler is not', owner.dropped == [True] and len(owner.calls) == 1)

owner = Owner()
scope = RequestScope()
ticket = scope.begin()
callback = ticket.callback(owner.handle, on_drop=Owner.on_drop)
ticket.cancellable.cancel()
callback('client', 'result')
check('a cancelled request is dropped by the callback too',
      owner.calls == [] and owner.dropped == [True])


# Real tasks: a cancelled request still calls back, and finishing it raises
def run_task(ticket, value, cancel_after_return=False):
    """A real Gio.Task over the ticket's cancellable whose callback is the
    ticket's. The handler decodes; decoding a cancelled task raises, so an
    outcome of ('error', ...) would be a bug."""
    outcome = []

    class Page(GObject.Object):
        def on_answer(self, source, result):
            try:
                outcome.append(('value', result.propagate_boolean()))
            except GLib.Error as error:
                outcome.append(('error', error))

    page = Page()
    task = Gio.Task.new(GObject.Object(), ticket.cancellable,
                        ticket.callback(page.on_answer), None)
    task.return_boolean(value)
    if cancel_after_return:
        ticket.cancellable.cancel()
    spin()
    return outcome, page


scope = RequestScope()
outcome, page = run_task(scope.begin(), True)
check('a real answer of the current generation reaches the handler and '
      'decodes', outcome == [('value', True)], repr(outcome))

scope = RequestScope()
ticket = scope.begin()
outcome, page = run_task(ticket, True, cancel_after_return=True)
check('real answer then cancel: dropped before the handler decodes '
      '(decoding would raise CANCELLED), nothing raised', outcome == [],
      repr(outcome))

scope = RequestScope()
ticket = scope.begin()
scope.begin()
outcome, page = run_task(ticket, True)
check('a superseded real task is dropped without reaching the handler',
      outcome == [], repr(outcome))

scope = RequestScope()
ticket = scope.begin()
scope.close()
outcome, page = run_task(ticket, True)
check('a task finished after close is dropped', outcome == [], repr(outcome))

# A CANCELLED error the scope did not cause is the reader's to see
scope = RequestScope()
owner = Owner()
ticket = scope.begin()
callback = ticket.callback(owner.handle)
foreign = GLib.Error.new_literal(Gio.io_error_quark(), 'Operation was '
                                 'cancelled', Gio.IOErrorEnum.CANCELLED)
callback('client', foreign)
check('a CANCELLED error from a cancellable the scope did not cancel '
      'reaches the handler', owner.calls == [('client', foreign)])


# No reference that keeps an owner alive
late_calls = []


class Page(GObject.Object):
    def handle(self, client, result):
        late_calls.append(result)


page = Page()
page.scope = RequestScope()
held = [page.scope.begin(), page.scope.start()]
held_callback = held[0].callback(page.handle,
                                 on_drop=lambda owner: late_calls.append('d'))
weak = page.weak_ref()
del page
gc.collect()
check('a scope, its tickets and a callback do not keep their owner alive',
      weak() is None)
held_callback('client', 'result')
check('a callback whose owner was freed calls neither handler nor on_drop',
      late_calls == [])
check('the scope holds only plain state',
      all(isinstance(value, (int, bool, Gio.Cancellable))
          for value in vars(held[0]._scope).values()))


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

if failures:
    print('\n%d check(s) failed' % len(failures))
    sys.exit(1)
