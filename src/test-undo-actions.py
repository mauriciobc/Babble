#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Undo before a destructive request: deleting a post, and the boost
that an unboost takes away.

Pins what feedback.deferred_action promises (one of commit and restore, once;
flush on close; no window means commit now), that a taken row goes back to
its place, and that the menu's delete sends nothing until its toast goes.

Exits 77 (skip) without a display or a compiled bundle."""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

Adw.init()

from src import feedback, pagenav  # noqa: E402
from src.entities import lift  # noqa: E402
from src.statusactions import StatusActions  # noqa: E402
from src.statusmenu import StatusMenuButton  # noqa: E402
from src.timelinedata import StatusItem, TimelineModel  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


class FakeToasts:
    """Stands in for feedback.show_toast; holds the callbacks the real toast
    would have, so the test can press Undo or let it time out."""

    def __init__(self, shown=True):
        self.shown = shown
        self.toasts = []

    def __call__(self, widget, text, undo=None, undo_label=None,
                 dismissed=None):
        # A widget with no root finds no window, as in GTK.
        if not self.shown or getattr(widget, 'unrooted', False):
            return None
        self.toasts.append({'text': text, 'undo': undo,
                            'dismissed': dismissed})
        return object()

    def press_undo(self):
        toast = self.toasts[-1]
        toast['undo']()
        toast['dismissed']()  # libadwaita dismisses after the button

    def time_out(self):
        self.toasts[-1]['dismissed']()


real_toast = feedback.show_toast
toasts = FakeToasts()
feedback.show_toast = toasts

# The helper

log = []
feedback.deferred_action(None, 'Gone', lambda: log.append('commit'),
                      lambda: log.append('restore'))
check('nothing happens while the toast is up', log == [])
toasts.time_out()
check('timing out commits', log == ['commit'])
toasts.time_out()
check('a second dismissal does nothing', log == ['commit'])

log.clear()
feedback.deferred_action(None, 'Gone', lambda: log.append('commit'),
                      lambda: log.append('restore'))
toasts.press_undo()
check('Undo restores and does not commit', log == ['restore'])

log.clear()
feedback.deferred_action(None, 'Gone', lambda: log.append('commit'),
                      lambda: log.append('restore'))
feedback.flush_deferred()
toasts.time_out()
check('flushing commits once', log == ['commit'])
check('nothing is left waiting', feedback._pending_actions == [])

toasts.shown = False
log.clear()
feedback.deferred_action(None, 'Gone', lambda: log.append('commit'),
                      lambda: log.append('restore'))
check('without a window it commits at once', log == ['commit'])
toasts.shown = True

# The model gives a row back where it was


def status(identifier):
    return {'id': identifier, 'content': 'p%s' % identifier,
            'account': {'acct': 'a', 'id': '1'},
            'created_at': '2026-01-01T00:00:00.000Z'}


def ids(model):
    return [model.get_item(i).status_id for i in range(model.get_n_items())]


model = TimelineModel()
model.append_old([status('5'), status('4'), status('3'), status('2')])
taken = model.take_status('4')
check('take removes the post', ids(model) == ['5', '3', '2'])
model.put_back(taken)
check('put_back restores the position', ids(model) == ['5', '4', '3', '2'])
model.put_back(taken)
check('put_back twice does not duplicate', ids(model) == ['5', '4', '3', '2'])
check('taking an unknown id takes nothing', model.take_status('99') == [])

# The re-boost behind an unboost's Undo


class Rebooster:
    """The slice of StatusActions that _reboost uses."""

    def __init__(self, client, item):
        self.client, self.item, self.refreshed = client, item, 0

    def _refresh(self):
        self.refreshed += 1


class Answering:
    def __init__(self):
        self.requests = []

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.requests.append((method, path))
        callback(self, lift(dict(status('7'), reblogged=True)), None)

    def request_finish(self, result):
        return result


reblogged = StatusItem(status('7'))
answering = Answering()
actions = Rebooster(answering, reblogged)
StatusActions._reboost(actions, reblogged)
check('Undo for an unboost posts the reblog again',
      answering.requests == [('POST', '/statuses/7/reblog')])
check('the row is refreshed while it shows the post',
      actions.refreshed == 1)
actions.item = None
StatusActions._reboost(actions, reblogged)
check('a recycled row is left alone', actions.refreshed == 1)


# The menu's delete


class Client:
    def __init__(self):
        self.requests = []

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        self.requests.append((method, path))

    def request_finish(self, result):
        raise result


class Page:
    def __init__(self, model):
        self.model = model


class Menu:
    """The slice of StatusMenuButton that _delete_with_undo uses."""

    def __init__(self, page, client):
        self.page, self.client = page, client
        self.errors = []

    def get_root(self):
        return None

    def _timeline_page(self):
        return self.page



def delete(menu, item):
    StatusMenuButton._delete_with_undo(menu, item)


model = TimelineModel()
model.append_old([status('5'), status('4'), status('3')])
client = Client()
menu = Menu(Page(model), client)
item = model.get_item(1)
delete(menu, item)
check('delete takes the row out of view', ids(model) == ['5', '3'])
check('delete sends nothing yet', client.requests == [])
toasts.press_undo()
check('Undo puts the row back', ids(model) == ['5', '4', '3'])
check('Undo sends nothing', client.requests == [])

delete(menu, item)
toasts.time_out()
check('timing out sends one DELETE',
      client.requests == [('DELETE', '/statuses/4')], str(client.requests))
check('and the row stays gone', ids(model) == ['5', '3'])

model = TimelineModel()
model.append_old([status('5'), status('4')])
client = Client()
menu = Menu(Page(model), client)
delete(menu, model.get_item(0))
feedback.flush_deferred()
check('closing the window sends the DELETE',
      client.requests == [('DELETE', '/statuses/5')], str(client.requests))

# Removing the row unparents the menu button, and an unparented widget finds
# no window to show the toast on: the delete used to go straight through.
model = TimelineModel()
model.append_old([status('5'), status('4')])
client = Client()
menu = Menu(Page(model), client)
menu.unrooted = False
_take = model.take_status


def _take_and_unroot(status_id):
    menu.unrooted = True
    return _take(status_id)


model.take_status = _take_and_unroot
_window = object()
_window_of = pagenav.window_of
pagenav.window_of = lambda widget: _window
delete(menu, model.get_item(0))
check('the toast is shown although the row is gone', len(toasts.toasts) > 0
      and client.requests == [], str(client.requests))
toasts.press_undo()
check('and Undo still works', ids(model) == ['5', '4'])
pagenav.window_of = _window_of

# A post shown outside a timeline list (a thread, a search result) is hidden
# by its host and shown again on Undo; the DELETE still waits.
events = []
client = Client()
menu = Menu(None, client)
menu._notify = events.append
delete(menu, StatusItem(status('9')))
check('a post outside a list is hidden at once', events == ['deleted'])
check('and nothing is sent yet', client.requests == [])
toasts.press_undo()
check('Undo shows it again', events == ['deleted', 'restored'])
check('and sends nothing', client.requests == [])

events.clear()
delete(menu, StatusItem(status('9')))
toasts.time_out()
check('timing out sends the DELETE',
      client.requests == [('DELETE', '/statuses/9')], str(client.requests))
check('and the post stays hidden', events == ['deleted'])

feedback.show_toast = real_toast

# The model's treatment of plain rows, boost rows and boost carousels. These
# pin what take_status, remove_status, trim and replace_status do with each
# kind of row, so the model can answer them through the items themselves.
from src.timelinedata import BoostCarouselItem  # noqa: E402


def boost(wrapper_id, boosted_id):
    """A boost wrapper whose 'reblog' is the boosted status."""
    wrapper = dict(status(wrapper_id), account={'acct': 'b', 'id': '9'})
    wrapper['reblog'] = status(boosted_id)
    return wrapper


def carousel(*pairs):
    wrappers = [boost(w, b) for w, b in pairs]
    return BoostCarouselItem([w['reblog'] for w in wrappers], wrappers)


def shape(model):
    """The store: (status_id, raw id) for a row, a tuple of boosted ids and
    one of wrapper ids for a carousel."""
    out = []
    for i in range(model.get_n_items()):
        item = model.get_item(i)
        if isinstance(item, BoostCarouselItem):
            out.append((tuple(s['id'] for s in item.boosts),
                        tuple(w['id'] for w in item.wrappers)))
        else:
            out.append((item.status_id, item.raw['id']))
    return out


def build(*items):
    model = TimelineModel()
    for item in items:
        model._store.append(item)
        model._register_seen(item)
    return model


def plain(identifier):
    return StatusItem(status(identifier))


def boost_row(wrapper_id, boosted_id):
    return StatusItem(boost(wrapper_id, boosted_id))


# 1. One id as a plain row, a boost row and inside a carousel
original, boost_of = plain('10'), boost_row('30', '10')
inside = carousel(('20', '10'), ('21', '11'))
model = build(original, inside, boost_of)
taken = model.take_status('10')
check('take: returns the plain rows by position, not the carousel',
      [(p, i) for p, i in taken] == [(0, original), (2, boost_of)],
      repr([(p, i.raw['id']) for p, i in taken]))
check('take: the carousel keeps what is not the id',
      shape(model) == [(('11',), ('21',))], repr(shape(model)))
check('take: the carousel is the same row',
      model.get_item(0) is inside)
check('take: nothing taken from a carousel alone leaves it',
      build(carousel(('20', '10'), ('21', '11'))).take_status('11') == [])
emptied = build(carousel(('20', '10')))
check('take: a carousel emptied by the take is not a taken row',
      emptied.take_status('10') == [] and emptied.get_n_items() == 0,
      repr(shape(emptied)))

# 2. Several plain rows, then put_back restores the order
model = build(plain('12'), plain('10'), plain('13'), boost_row('30', '10'),
              plain('14'))
before = shape(model)
taken = model.take_status('10')
check('take: several rows come back in position order',
      [p for p, _i in taken] == [1, 3], repr([p for p, _i in taken]))
check('take: the store is left with the others',
      shape(model) == [('12', '12'), ('13', '13'), ('14', '14')],
      repr(shape(model)))
model.put_back(taken)
# Both rows answer to status_id '10': put_back decides "already back" from
# the ids seen when it starts, so the second one is restored as well.
check('put_back restores every taken row at its original position',
      shape(model) == before, repr(shape(model)))
check('put_back sees the ids of both rows again',
      '10' in model._seen and {i.status_id for _p, i in taken} <= model._seen)
model.put_back(taken)
check('a second put_back of the same rows changes nothing',
      shape(model) == before, repr(shape(model)))

# The post came back some other way (a refresh, a stream insert) before Undo:
# its id is seen when put_back starts, so no taken row is put back, the boost
# row of it included (it stays gone until the next refresh; no duplicates).
model = build(plain('12'), plain('10'), plain('13'), boost_row('30', '10'),
              plain('14'))
taken = model.take_status('10')
returned = plain('10')
model._store.insert(0, returned)
model._register_seen(returned)
after_return = shape(model)
model.put_back(taken)
check('put_back adds nothing for a post that came back before Undo',
      shape(model) == after_return and model.get_item(0) is returned,
      repr(shape(model)))
model = build(plain('12'), plain('10'), plain('13'), boost_row('30', '10'),
              plain('14'))
taken = model.take_status('30')
check('take by wrapper id takes only the boost row',
      [(p, i.raw['id']) for p, i in taken] == [(3, '30')]
      and shape(model) == [('12', '12'), ('10', '10'), ('13', '13'),
                           ('14', '14')], repr(shape(model)))
model.put_back(taken)
check('put_back restores the boost row to its place', shape(model) == before,
      repr(shape(model)))

# 3. The only wrapper of a carousel
for target in ('60', '61'):
    model = build(plain('50'), carousel(('60', '61')))
    result = model.remove_status(target)
    check('remove %s: the emptied carousel leaves the store' % target,
          result is True and shape(model) == [('50', '50')],
          repr((result, shape(model))))

# 4. One of several wrappers
for target in ('62', '63'):
    two = carousel(('60', '61'), ('62', '63'), ('64', '65'))
    model = build(two)
    count = []
    two.connect('changed', lambda _i, seen=count: seen.append(1))
    result = model.remove_status(target)
    check('remove %s: the carousel keeps the rest in step' % target,
          result is True and shape(model) == [(('61', '65'), ('60', '64'))]
          and model.get_item(0) is two, repr((result, shape(model))))
    check('remove %s: changed is emitted once' % target, count == [1],
          repr(count))

# 5. An unknown id
model = build(plain('50'), carousel(('60', '61')), boost_row('70', '71'))
before = shape(model)
check('remove: an unknown id returns False and changes nothing',
      model.remove_status('999') is False and shape(model) == before,
      repr(shape(model)))
check('remove: and take returns nothing', model.take_status('999') == [])

# 6. trim with a carousel among the rows left
model = build(plain('90'), plain('80'), carousel(('70', '71'), ('75', '76')),
              plain('60'), boost_row('55', '71'), plain('50'))
check('trim: drops what follows', model.trim(3) is True
      and shape(model) == [('90', '90'), ('80', '80'),
                           (('71', '76'), ('70', '75'))],
      repr(shape(model)))
check('trim: oldest is the least wrapper id of the carousel',
      model.oldest_id == '70', repr(model.oldest_id))
check('trim: seen drops only ids with no copy left',
      model._seen == {'90', '80', '70', '71', '75', '76'},
      repr(sorted(model._seen)))
model = build(plain('100'), carousel(('99', '98')), plain('5'))
model.trim(2)
check('trim: oldest compares ids as numbers across rows',
      model.oldest_id == '99', repr(model.oldest_id))

# 7. replace_status
inside = carousel(('20', '12'))
row, wrapped = plain('10'), boost_row('30', '11')
model = build(row, wrapped, inside)
changes = []
inside.connect('changed', lambda _i: changes.append('carousel'))
row.connect('changed', lambda _i: changes.append('row'))
wrapped.connect('changed', lambda _i: changes.append('boost'))
check('replace: a post inside a carousel',
      model.replace_status({'id': '12', 'content': 'edited'}) is True
      and inside.boosts[0]['content'] == 'edited'
      and inside.wrappers[0]['reblog']['content'] == 'p12'
      and model.get_item(2) is inside and changes == ['carousel'],
      repr((changes, inside.boosts)))
changes.clear()
check('replace: a plain row',
      model.replace_status({'id': '10', 'content': 'edited'}) is True
      and row.raw['content'] == 'edited' and model.get_item(0) is row
      and changes == ['row'], repr((changes, row.raw)))
changes.clear()
check('replace: a boost row edits the boosted post',
      model.replace_status({'id': '11', 'content': 'edited'}) is True
      and wrapped.raw['id'] == '30' and wrapped.raw['content'] == 'p30'
      and wrapped.raw['reblog']['content'] == 'edited'
      and wrapped.status['content'] == 'edited'
      and wrapped.is_boost and changes == ['boost'],
      repr((changes, wrapped.raw)))
check('replace: a wrapper id is not the post',
      model.replace_status({'id': '30', 'content': 'x'}) is False)
check('replace: an unknown id is False',
      model.replace_status({'id': '999', 'content': 'x'}) is False)
check('replace: an empty id is False',
      model.replace_status({'content': 'x'}) is False)
first, second = carousel(('20', '11')), boost_row('30', '11')
model = build(first, second)
check('replace: the first match wins',
      model.replace_status({'id': '11', 'content': 'edited'}) is True
      and first.boosts[0]['content'] == 'edited'
      and second.status['content'] == 'p11')

if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
