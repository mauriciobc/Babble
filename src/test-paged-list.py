#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The paging protocol of ``pages.PagedList``, driven directly.

A real ``PagedList`` runs against a fake client: it records every request
the list sends through ``request_async`` and answers each one on demand,
so the test controls the order of the responses. Nothing touches the
network. What is pinned is the invariant the list exists to keep --
``rows``, ``list_box`` and the visible page of ``stack`` move together --
and the operations that callers used to perform by hand: appending a
row, filtering a page client-side, a placeholder row in place of the
empty state. It also proves a list and its rows are freed once dropped,
and (the last checks) that the test itself can fail. Exits 77 (skip)
without a display."""

import gc
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

Adw.init()

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
    def __init__(self, method, path, params, callback):
        self.method = method
        self.path = path
        self.params = params
        self.callback = callback


class FakeClient:
    """The two calls ``PagedList`` makes of a client."""

    def __init__(self):
        self.sent = []
        self.finished = 0

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.sent.append(Request(method, path, dict(params or {}),
                                 callback))

    def request_finish_paged(self, result):
        self.finished += 1
        if result[0] == 'error':
            raise GLib.Error.new_literal(
                Gio.io_error_quark(), result[1], Gio.IOErrorEnum.FAILED)
        return result[1], result[2], None

    def answer(self, items, next_params=None, request=None):
        request = request or self.sent[-1]
        request.callback(self, ('ok', items, next_params), None)

    def fail(self, request=None, message='boom'):
        request = request or self.sent[-1]
        request.callback(self, ('error', message), None)


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


reported = []
real_report = pages.report_error
pages.report_error = lambda widget, heading, error: reported.append(
    (heading, error.message))

# First page

client, lst = build()
check('the first request is sent at construction, by 40 and the path',
      len(client.sent) == 1 and client.sent[0].path == '/things'
      and client.sent[0].method == 'GET'
      and client.sent[0].params == {'limit': 40})
check('a first page in flight shows the loading page',
      page_name(lst) == 'loading')
client.answer([item(1), item(2)])
check('a first page shows the list with its rows in order',
      page_name(lst) == 'list' and texts(lst) == ['row 1', 'row 2']
      and in_step(lst))

client, lst = build()
client.answer([])
check('an empty first page shows the empty page',
      page_name(lst) == 'empty' and lst.rows == [])

client, lst = build(params={'q': 'x'}, paginated=False)
check('a list that does not page sends no limit',
      client.sent[0].params == {'q': 'x'})

client, lst = build()
client.fail()
check('a failed first page shows the error page',
      page_name(lst) == 'error' and lst.rows == [])
lst._on_retry(None)
check('Retry reloads: a second request, the loading page',
      len(client.sent) == 2 and page_name(lst) == 'loading')
client.answer([item(1)])
check('after Retry a good page shows the list',
      page_name(lst) == 'list' and texts(lst) == ['row 1'])

# load_more

client, lst = build()
client.answer([item(1), item(2)], {'max_id': '2'})
check('a page with a next link does not ask for more by itself here',
      len(client.sent) == 1)
lst.load_more()
check('load_more follows next: the params of the next link, the spinner shows',
      len(client.sent) == 2
      and client.sent[1].params == {'max_id': '2'}
      and lst.more_spinner.get_visible())
lst.load_more()
check('load_more does nothing while a request is out',
      len(client.sent) == 2)
client.answer([item(3)], None)
check('the next page is appended and the spinner hides',
      texts(lst) == ['row 1', 'row 2', 'row 3']
      and not lst.more_spinner.get_visible() and in_step(lst))
lst.load_more()
check('load_more stops when there is no next link', len(client.sent) == 2)

client, lst = build()
client.answer([item(1), item(2)], {'max_id': '2'})
lst.load_more()
client.fail()
check('a failed later page keeps the rows and the list',
      page_name(lst) == 'list' and texts(lst) == ['row 1', 'row 2'])
check('a failed later page reports the error, not the error page',
      reported and reported[-1][0] == 'Could not load more'
      and not lst.more_spinner.get_visible())
lst.load_more()
check('the cursor survives a failed later page: it can be retried',
      len(client.sent) == 3
      and client.sent[2].params == {'max_id': '2'})

client, lst = build(fill=True)
client.answer([item(1)], {'max_id': '1'})
settle()
check('a short first page that cannot scroll keeps filling',
      len(client.sent) == 2
      and client.sent[1].params == {'max_id': '1'})

# A superseded refresh


def superseded_ok(cls):
    client = FakeClient()
    lst = cls(client, '/things', make_row)
    first = client.sent[0]
    lst.refresh()
    second = client.sent[1]
    client.answer([item(1)], request=first)
    stale_dropped = page_name(lst) == 'loading' and lst.rows == []
    client.answer([item(2), item(3)], request=second)
    return (stale_dropped and texts(lst) == ['row 2', 'row 3']
            and page_name(lst) == 'list' and in_step(lst))


check('a response after a newer refresh() is dropped, the newer state '
      'is intact', superseded_ok(pages.PagedList))

client, lst = build()
client.answer([item(1)], {'max_id': '1'})
lst.load_more()
late = client.sent[-1]
lst.refresh()
client.answer([item(9)], None, request=late)
check('a later page that outlived a refresh does not touch the list',
      lst.rows == [] and page_name(lst) == 'loading'
      and lst.more_spinner.get_visible() is True)
client.answer([item(2)])
check('the refresh lands after the stale page', texts(lst) == ['row 2'])

# The per-page filter and the decoder

client = FakeClient()
decoded = []


def finish(client_, result):
    decoded.append(result[0])
    return client_.request_finish_paged(result)


def even(items):
    return [data for data in items if int(data['id']) % 2 == 0]


client, lst = build(client, finish=finish, page_filter=even)
client.answer([item(1), item(3)], {'max_id': '3'})
check('a page that filters to nothing while more follow asks for the next',
      len(client.sent) == 2
      and client.sent[1].params == {'max_id': '3'}
      and page_name(lst) == 'loading' and lst.rows == [])
client.answer([item(5)], {'max_id': '5'})
check('and again, with no row and the loading page kept',
      len(client.sent) == 3 and page_name(lst) == 'loading')
client.answer([item(6), item(7)], None)
check('the first page with a match shows only the matches',
      page_name(lst) == 'list' and texts(lst) == ['row 6'])
check('the supplied decoder reads every page', decoded == ['ok'] * 3)

client, lst = build(page_filter=even)
client.answer([item(1)], {'max_id': '1'})
client.answer([item(3)], None)
check('pages that run out while all filter away end on the empty page',
      page_name(lst) == 'empty' and len(client.sent) == 2
      and lst.rows == [])

client, lst = build(page_filter=even)
client.answer([item(1)], {'max_id': '1'})
client.fail()
check('a failed page in the filter loop shows the error page',
      page_name(lst) == 'error')

client, lst = build(finish=lambda c, r: ([item(2)], None, None))
client.sent[0].callback(client, None, None)
check('the decoder is the one supplied, not the client\'s',
      texts(lst) == ['row 2'] and client.finished == 0)

# make_row returning None

client, lst = build()
client.answer([item(1), item(2, skip=True), item(3)])
check('make_row returning None skips the item',
      texts(lst) == ['row 1', 'row 3'] and in_step(lst))
client, lst = build()
client.answer([item(1, skip=True)])
check('a page of skipped items shows the empty page',
      page_name(lst) == 'empty')

# append, prepend, remove

client, lst = build()
client.answer([])
first = Adw.ActionRow(title='a')
lst.append_row(first)
check('append_row from the empty page shows the list with the row',
      page_name(lst) == 'list' and lst.rows == [first] and in_step(lst))
second = Adw.ActionRow(title='b')
lst.append_row(second)
zero = Adw.ActionRow(title='z')
lst.prepend_row(zero)
check('append and prepend keep rows and list box in order',
      lst.rows == [zero, first, second] and in_step(lst))
lst.remove_row(first)
lst.remove_row(first)
check('remove_row drops one row, and an unknown row is harmless',
      lst.rows == [zero, second] and in_step(lst)
      and page_name(lst) == 'list')
lst.remove_row(zero)
lst.remove_row(second)
check('removing the last row shows the empty page',
      page_name(lst) == 'empty' and children(lst) == [])

client, lst = build()
lst.prepend_row(Adw.ActionRow(title='p'))
check('prepend_row onto a loading list shows the list',
      page_name(lst) == 'list')

client, lst = build()
client.answer([item(1)], {'max_id': '1'})
del lst._maybe_fill  # the real idle fill, from here on
settle()
lst.remove_row(lst.rows[0])
sent = len(client.sent)
check('removing the last row while more pages exist keeps the list',
      page_name(lst) == 'list' and lst.rows == [] and in_step(lst))
settle()
check('... and asks for the next page', len(client.sent) == sent + 1)

# The placeholder


def make_placeholder():
    row = Adw.ActionRow(title='none yet')
    return row


client, lst = build(make_placeholder=make_placeholder)
client.answer([])
check('an empty first page shows the placeholder instead of the empty '
      'page', page_name(lst) == 'list' and lst.rows == []
      and children(lst) == [lst.placeholder])
placeholder = lst.placeholder
lst.append_row(Adw.ActionRow(title='a'))
check('a row removes the placeholder',
      lst.placeholder is None and len(children(lst)) == 1
      and in_step(lst) and placeholder.get_parent() is None)
lst.remove_row(lst.rows[0])
check('removing the last row restores one placeholder',
      page_name(lst) == 'list' and lst.placeholder is not None
      and children(lst) == [lst.placeholder])
lst.prepend_row(Adw.ActionRow(title='b'))
check('prepend_row removes the placeholder too',
      lst.placeholder is None and len(children(lst)) == 1)
lst.remove_row(lst.rows[0])
lst.refresh()
check('refresh drops the placeholder and shows loading',
      lst.placeholder is None and children(lst) == []
      and page_name(lst) == 'loading')
client.answer([])
check('and an empty page afterwards shows one placeholder again',
      len(children(lst)) == 1 and page_name(lst) == 'list')
lst.refresh()
client.answer([item(1)])
check('a refreshed page with items shows rows and no placeholder',
      lst.placeholder is None and texts(lst) == ['row 1'] and in_step(lst))

client, lst = build(make_placeholder=make_placeholder)
client.fail()
check('a failed first page shows the error page, not the placeholder',
      page_name(lst) == 'error' and children(lst) == [])

client, lst = build(make_placeholder=make_placeholder)
client.answer([item(1)], {'max_id': '1'})
del lst._maybe_fill
settle()
lst.remove_row(lst.rows[0])
check('with more pages, the last row removed shows the placeholder',
      page_name(lst) == 'list' and children(lst) == [lst.placeholder])
sent = len(client.sent)
settle()
check('... while the next page is asked for', len(client.sent) == sent + 1)

client, lst = build(make_placeholder=lambda: None)
client.answer([])
check('a placeholder hook that returns nothing falls back to the empty '
      'page', page_name(lst) == 'empty')

# The shared near-bottom rule

check('PagedPosts and PagedList share one preload distance',
      pages.PagedPosts.PRELOAD == pages.PRELOAD == 800)
adjustment = Gtk.Adjustment(value=0, lower=0, upper=2000, page_size=100)
check('near_bottom: far from the end is not near',
      not pages.near_bottom(adjustment))
adjustment.set_value(1100)
check('near_bottom: within the preload of the end is near',
      pages.near_bottom(adjustment)
      and pages.PagedPosts().on_scroll_near_end(adjustment))
check('near_bottom takes a distance',
      not pages.near_bottom(Gtk.Adjustment(value=0, lower=0, upper=2000,
                                           page_size=100), 100))

# Freed once dropped


class Owner(Adw.Bin):
    """A page that hands its list weak hooks, as the callers do."""

    def __init__(self):
        super().__init__()
        self.client = FakeClient()
        self.list = pages.PagedList(
            self.client, '/things', pages.weak_method(self._row),
            page_filter=pages.weak_method(self._keep),
            make_placeholder=pages.weak_method(self._placeholder))
        self.set_child(self.list)

    def _row(self, data):
        return make_row(data)

    def _keep(self, items):
        return items

    def _placeholder(self):
        return Adw.ActionRow(title='none yet')


client, lst = build(page_filter=even, make_placeholder=make_placeholder)
client.answer([item(2), item(4)], {'max_id': '4'})
extra = Adw.ActionRow(title='x')
lst.append_row(extra)
refs = [lst.weak_ref(), extra.weak_ref(), lst.rows[0].weak_ref(),
        lst.scrolled.weak_ref()]
client.sent.clear()
lst.remove_row(extra)
del extra
for row in list(lst.rows):
    lst.remove_row(row)
refs.append(lst.placeholder.weak_ref())
del lst, client, row
settle()
check('a list, its rows and its placeholder are freed once dropped, '
      'with a filter and a placeholder set',
      all(freed(ref) for ref in refs))

owner = Owner()
owner.client.answer([item(1), item(2)], None)
owner.list.remove_row(owner.list.rows[0])
owner.list.remove_row(owner.list.rows[0])
refs = [owner.weak_ref(), owner.list.weak_ref(),
        owner.list.placeholder.weak_ref()]
owner.client.sent.clear()
del owner
settle()
check('a page whose list holds weak hooks to it is freed with its list',
      all(freed(ref) for ref in refs))

# The test can fail


class NoGuard(pages.PagedList):
    """PagedList without its generation guard: a stale page lands."""

    def _on_loaded(self, client, result, generation):
        super()._on_loaded(client, result, self._generation)


check('self-check: the superseded-refresh check fails on a list with no '
      'generation guard', not superseded_ok(NoGuard))
check('self-check: the stack check can tell the empty page from the list',
      page_name(build()[1]) != 'list')
check('self-check: the order check fails on a wrong order',
      not (lambda c, lst: (c.answer([item(1), item(2)]),
                           texts(lst) == ['row 2', 'row 1'])[1])(*build()))

pages.report_error = real_report

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all paged list checks passed')
