# SPDX-License-Identifier: GPL-3.0-or-later

"""The requests one owner (a page) has in flight, and whether an answer
to one of them is still wanted.

A page that reloads must ignore the answer to the load it replaced, and a
page that goes away must stop what it started. Both are one question asked
when an answer arrives, and one cancel when the answer stops being wanted:
this module is that, so a page keeps no generation counter of its own.

Use, for READS only (a write cannot be undone by cancelling it: the server
may already have acted, so a write is never started through a scope)::

    scope = RequestScope()                 # one per owner; holds no page
    ticket = scope.begin()                 # a new generation: whatever was
                                           # started before is cancelled
    api.some_async(client, callback=cb, cancellable=ticket.cancellable)
    ...
    def cb(client, result, _data=None):
        if not ticket.wanted():            # FIRST, before decoding
            return
        try:
            data = api.some_finish(client, result)
        except GLib.Error as error:
            if not ticket.wanted(error):   # a CANCELLED error is never wanted
                return
            ...

* :meth:`RequestScope.begin` supersedes the current generation: its
  cancellable is cancelled and a fresh one is made (a cancelled
  cancellable stays cancelled, so a generation never reuses one).
* :meth:`RequestScope.start` makes a request in the CURRENT generation, for
  a follow-up of it (the next page of a list). Every ticket of one
  generation shares its cancellable, so the next ``begin`` cancels them
  together. A scope starts with a generation of its own, so ``start`` before
  any ``begin`` is valid.
* :meth:`Ticket.wanted` is the one question. Ask it at the top of the
  callback, BEFORE decoding: a ``Gio.Task`` whose cancellable fired reports
  CANCELLED on propagate even when the real answer had already arrived, so
  decoding such an answer raises, and a request that was cancelled still
  calls its callback, sometimes seconds later (the transport's retry
  back-off). The question is safe for a success, an error and a CANCELLED
  error alike, and it never raises. Passing the error is an extra net for a
  CANCELLED that this scope did not cause; asking first already covers
  every cancel the scope made.
* :meth:`RequestScope.close` cancels everything outstanding and makes every
  later answer unwanted. Closing twice is harmless. A closed scope never
  raises on ``begin`` or ``start``: it hands out a ticket that is never
  wanted and whose cancellable is already cancelled, so a request started
  by a late idle fails at once and is dropped silently. Check
  :attr:`RequestScope.closed` to not send it at all.

The scope holds an int, a flag and a ``Gio.Cancellable``: no page, widget,
window or client, and no state outside the instance. A ticket holds its
scope and nothing else, so a request in flight keeps neither page nor
scope's owner alive."""

from gi.repository import Gio, GLib


def is_cancelled_error(error):
    """True for the CANCELLED error a cancelled request reports."""
    return (isinstance(error, GLib.Error)
            and error.matches(Gio.io_error_quark(),
                              Gio.IOErrorEnum.CANCELLED))


class Ticket:
    """One request's place in a scope: the cancellable to hand the api
    call, and the question of whether its answer is still wanted."""

    __slots__ = ('_scope', '_generation', 'cancellable')

    def __init__(self, scope, generation, cancellable):
        self._scope = scope
        self._generation = generation
        self.cancellable = cancellable

    def wanted(self, error=None):
        """False when the answer must be dropped silently: the scope was
        closed, a newer generation began, the request was cancelled, or
        *error* is a CANCELLED error. Never raises."""
        scope = self._scope
        if scope._closed or scope._generation != self._generation:
            return False
        if self.cancellable.is_cancelled():
            return False
        return error is None or not is_cancelled_error(error)


class RequestScope:
    """The generations of one owner's reads; see the module docstring."""

    def __init__(self):
        self._closed = False
        self._generation = 0
        self._cancellable = Gio.Cancellable()

    @property
    def closed(self):
        return self._closed

    def begin(self):
        """Start a new generation, cancelling the previous one, and return
        its first ticket."""
        if self._closed:
            return self._dead()
        self._cancellable.cancel()
        self._generation += 1
        self._cancellable = Gio.Cancellable()
        return Ticket(self, self._generation, self._cancellable)

    def start(self):
        """Return a ticket in the current generation."""
        if self._closed:
            return self._dead()
        return Ticket(self, self._generation, self._cancellable)

    def close(self):
        """Cancel what is outstanding and drop every later answer."""
        if self._closed:
            return
        self._closed = True
        self._cancellable.cancel()

    def _dead(self):
        cancellable = Gio.Cancellable()
        cancellable.cancel()
        return Ticket(self, -1, cancellable)
