# SPDX-License-Identifier: GPL-3.0-or-later

"""The paged-list test's harness: a fake client and the list builders.

A real ``pages.PagedList`` runs against a fake client that models the
real transport's cancellation, and the checks compare what the list's
rows, box and visible page say after each answer. Test-only; never
installed.
"""

import gc

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib  # noqa: E402

from src import pages  # noqa: E402


failures = []
CONTEXT = GLib.MainContext.default()


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def settle():
    """Run what is queued on the main loop (the idle fill), not more."""
    for _attempt in range(20):
        if not CONTEXT.iteration(False):
            return


def freed(ref, attempts=5):
    for _attempt in range(attempts):
        if gc.collect() is not None:
            gc.collect()
        if ref() is None:
            return True
    return ref() is None


class Request:
    def __init__(self, method, path, params, callback, cancellable):
        self.method = method
        self.path = path
        self.params = params
        self.callback = callback
        self.cancellable = cancellable


class FakeClient:
    """The two calls ``PagedList`` makes of a client. It models the real
    transport's cancellation: a cancelled request still calls back, and
    finishing it raises CANCELLED even when its answer had arrived."""

    def __init__(self):
        self.sent = []
        self.finished = 0

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.sent.append(Request(method, path, dict(params or {}),
                                 callback, cancellable))

    def request_finish_paged(self, result):
        self.finished += 1
        if result[0] == 'cancelled':
            raise GLib.Error.new_literal(
                Gio.io_error_quark(), 'Operation was cancelled',
                Gio.IOErrorEnum.CANCELLED)
        if result[0] == 'error':
            raise GLib.Error.new_literal(
                Gio.io_error_quark(), result[1], Gio.IOErrorEnum.FAILED)
        return result[1], result[2], None

    def _deliver(self, request, result):
        if request.cancellable is not None \
                and request.cancellable.is_cancelled():
            result = ('cancelled',)
        request.callback(self, result, None)

    def answer(self, items, next_params=None, request=None):
        request = request or self.sent[-1]
        self._deliver(request, ('ok', items, next_params))

    def fail(self, request=None, message='boom'):
        request = request or self.sent[-1]
        self._deliver(request, ('error', message))


def item(number, **extra):
    data = {'id': str(number), 't': 'row %d' % number}
    data.update(extra)
    return data


def make_row(data):
    if data.get('skip'):
        return None
    return Adw.ActionRow(title=data['t'])


def build(client=None, fill=False, **kwargs):
    client = client or FakeClient()
    kwargs.setdefault('empty_title', 'Nothing')
    lst = pages.PagedList(client, '/things', make_row, **kwargs)
    if not fill:
        # The idle fill asks for the next page of a list that never
        # scrolled; tests that drive load_more by hand turn it off.
        lst._maybe_fill = lambda: GLib.SOURCE_REMOVE
    return client, lst


def page_name(lst):
    return lst.stack.get_visible_child_name()


def children(lst):
    out = []
    child = lst.list_box.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def texts(lst):
    return [row.get_title() for row in lst.rows]


def in_step(lst):
    """rows and list_box agree (a placeholder may be the one extra)."""
    boxed = children(lst)
    extra = [lst.placeholder] if lst.placeholder is not None else []
    return boxed == lst.rows + extra or boxed == extra + lst.rows
