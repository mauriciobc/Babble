# SPDX-License-Identifier: GPL-3.0-or-later
"""What the Feed Chef quality screens' tests share: a world of audit records
(the real Recorder's, changed only in the counts and rules a case needs), a
fake engine that answers each cook with one, a recipe page and a window, and
the helpers that find and wait for widgets. Importing it builds the window, so
a test selects ``GSETTINGS_BACKEND=memory``, points XDG_*_HOME at a temporary
folder and checks for a display first, as every test does. Never installed."""

import copy
import gc
import json
import os
import random
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402,F401

from feedchefaudit import fuzz  # noqa: E402,F401
from feedchefaudit.qualitykit import metrics, plain, stored  # noqa: E402,F401
from src import (feedback, feedchefbuilder,  # noqa: E402,F401
                 feedchefbuilderlive, feedchefexplain, feedcheflist,
                 feedchefpages, feedchefquality)
from src.feedchef import advice, contract, health, history  # noqa: E402,F401
from src.feedchef import optimizer, quality, recipeedit  # noqa: E402,F401
from src.feedchef import templates  # noqa: E402,F401
from src.feedchef import decisionlog as logmod  # noqa: E402,F401
from src.feedchef import storage as storagemod  # noqa: E402,F401
from src.feedchef.engine import FeedResult  # noqa: E402,F401
from src.feedchef.recipe import (Modifier, default_recipe,  # noqa: E402,F401
                                 recipe_to_dict, validate_for_import)
from src.feedchefnewrecipe import NewRecipeDialog  # noqa: E402,F401

_TMP = os.path.dirname(os.environ['XDG_DATA_HOME'])

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def pump(iterations=30):
    context = GLib.MainContext.default()
    for _ in range(iterations):
        while context.iteration(False):
            pass
        GLib.usleep(2000)


def pump_until(condition, seconds=8.0):
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(seconds * 1e6)
    while not condition() and GLib.get_monotonic_time() < deadline:
        while context.iteration(False):
            pass
        GLib.usleep(2000)
    return condition()


def freed(ref, attempts=8):
    for _attempt in range(attempts):
        gc.collect()
        pump(3)
        if ref() is None:
            return True
    return ref() is None


def descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


def find(widget, kind, predicate=lambda _w: True):
    return [w for w in descendants(widget)
            if isinstance(w, kind) and predicate(w)]


def dialogs(kind):
    """The dialogs of *kind* now open, found in the toplevels."""
    found = []
    for top in Gtk.Window.list_toplevels():
        found.extend(w for w in descendants(top) if isinstance(w, kind))
    return found


def settle():
    """Everything the quality worker was given is done and delivered."""
    done = []
    feedchefquality.worker().submit(lambda: 1, lambda r: done.append(r))
    pump_until(lambda: done)
    pump(5)


# --- the world: records, logs, a fake engine --------------------------------

LOGS = os.path.join(_TMP, 'logs')
logmod.set_root(LOGS)
storagemod.set_root(os.path.join(_TMP, 'recipes'))
NOW = datetime.fromisoformat('2026-01-02T12:00:00+00:00')

BASE = None
for seed in range(300):
    rng = random.Random(seed)
    fuzz_recipe = fuzz.random_recipe(rng)
    results = fuzz.cook(fuzz.World(rng, fuzz_recipe), fuzz_recipe, seed,
                        pages=1)
    found = results[0].logger.record
    if found and found['summary']['selected'] >= 6:
        BASE = found
        break
check('a real record with six or more shown posts was cooked',
      BASE is not None)
RULE = sorted(contract.DIET_IDS)[0]


def make_record(cook, kind='real', tag=None, minutes=None):
    """A copy of the real record as cook number *cook*: 'good' posts all
    have a lot of engagement, 'bad' ones fail a diet rule (so the recipe
    score is low); no freshness fade, so the counts alone decide."""
    record = copy.deepcopy(BASE)
    record['cook_id'] = '%08x' % cook
    record['page_index'] = 0
    record['hop'] = 0
    record['outcome'] = {'status': 'ok'}
    at = NOW + timedelta(minutes=cook if minutes is None else minutes)
    record['clock']['now_utc'] = at.isoformat()
    if tag:
        record['variant'] = tag
    if kind == 'real':
        return record
    record['recipe']['snapshot']['kitchen']['modifiers'] = []
    for entry in record['posts']:
        if kind == 'good':
            entry['facts']['fav'] = 500
        elif not entry.get('removal'):
            entry['rules'] = dict(entry.get('rules') or {})
            entry['rules'][RULE] = 'fail'
    return record


def write_cook(recipe_id, cook, kind):
    """Put a cook of *kind* in the decision log of *recipe_id*."""
    os.makedirs(LOGS, mode=0o700, exist_ok=True)
    name = 'session_%d_%s__c%08x_%06x.json' % (
        1700000000000 + cook, recipe_id, cook, cook)
    path = os.path.join(LOGS, name)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'audit': make_record(cook, kind)}, handle)
    os.utime(path, (1700000000 + cook, 1700000000 + cook))


check('a bad cook scores under 0.5 and a good one over 0.6',
      quality.cook_summary([make_record(1, 'bad')])['score'] < 0.5
      and quality.cook_summary([make_record(1, 'good')])['score'] >= 0.6)

CALLS = []
TOASTS = []
BRAIN = {'fn': lambda recipe, variant, max_id, peek: None}
ENGINES = []


class FakeEngine:
    def __init__(self):
        ENGINES.append(self)
        self.shut = False

    def init_preload(self):
        pass

    def cancel(self):
        pass

    def shutdown(self):
        self.shut = True

    def process_feed(self, recipe, *, max_id=None, on_done=None,
                     variant=None, peek=False, **_kwargs):
        CALLS.append(SimpleNamespace(
            recipe=copy.deepcopy(recipe), max_id=max_id, variant=variant,
            peek=peek))
        record = BRAIN['fn'](recipe, variant, max_id, peek)
        base = 9000 if max_id else 100
        ids = ['%05d' % (base + n) for n in range(6)]

        def deliver():
            on_done(FeedResult(
                items=[{'id': sid, 'content': '<p>x</p>', 'tags': [],
                        'account': {'acct': 'u%s' % sid, 'id': '1'},
                        'created_at': '2026-01-01T00:00:00.000Z'}
                       for sid in ids],
                next_page_max_id=None, has_more=not max_id,
                scope_oldest_id='00050' if not max_id else None,
                logger=SimpleNamespace(record=record,
                                       discard=lambda: None)))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)
        return 'fake'


class Client:
    token = None
    instance_info = None
    instance = 'https://example.org'


Adw.init()


def fake_toast(_widget, text, undo=None, undo_label=None, dismissed=None):
    TOASTS.append(SimpleNamespace(text=text, undo=undo, widget=_widget,
                                  undo_label=undo_label))
    return TOASTS[-1]


feedback.show_toast = fake_toast
feedchefpages.make_engine = lambda *_args: FakeEngine()
feedchefbuilderlive.make_live_cook = lambda _client, _account: SimpleNamespace(
    request=lambda _recipe, _done, _error: None, supersede=lambda: None,
    close=lambda: None)

# The analysis and the health run on the worker: note where.
THREADS = {'analyze': [], 'health_map': [], 'health_of': [], 'score': []}
real_analyze, real_map = history.analyze, history.health_map
real_of, real_score = history.health_of, optimizer.score_of


def watch(name, fn):
    def wrapper(*args, **kwargs):
        THREADS[name].append(threading.current_thread())
        return fn(*args, **kwargs)
    return wrapper


history.analyze = watch('analyze', real_analyze)
history.health_map = watch('health_map', real_map)
history.health_of = watch('health_of', real_of)
optimizer.score_of = watch('score', real_score)

account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
storage = feedchefbuilder.storage_for(account)
window = Gtk.Window(default_width=900, default_height=900)
window.present()
WORDS = feedchefexplain.WORDS
MAIN = threading.main_thread()


def open_page(recipe, brain=None):
    BRAIN['fn'] = brain or (lambda recipe, variant, max_id, peek:
                            make_record(len(CALLS), 'real', variant))
    page = feedchefpages.FeedChefTimelinePage(Client(), account, recipe)
    navview = Adw.NavigationView()
    navview.add(Adw.NavigationPage(title='Cook', child=page))
    window.set_child(navview)
    pump_until(lambda: page.model.get_n_items() > 0)
    settle()
    return page


def reset():
    feedchefquality.HEALTH.clear()
    feedchefquality.ALERTED.clear()
    del TOASTS[:]


SYN = metrics(total=20, avg=1, median=1)


def stub_analyze(recipe, live, others=None):
    return history.Analysis(health.assess([]), SYN,
                            advice.recommend(recipe, SYN), False)


def make_recipe():
    found = plain(scope='home', mods=[Modifier(
        type='freshness', decay_minutes=300)])
    found.id = 'opt-1'
    return found
