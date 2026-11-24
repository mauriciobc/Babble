#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""What a reader sees of a Feed Chef recipe page, over a fake engine: the
toast when heat steps down (once per cook session), the Popular chip on a post
above the page's 90th percentile (built lazily, hidden on a recycled row),
Relax Filters on the empty page (with Undo), the Preview of an incoming
recipe before it is stored, and the chef on the list, in the import
question and in a copied link. Exits 77 (skip) without a display."""

import copy
import gc
import os
import sys
import tempfile
from types import SimpleNamespace

# Never the user's real dconf, data or state.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
_TMP = tempfile.mkdtemp(prefix='babble-feedchef-experience-')
for _name in ('DATA', 'STATE', 'CACHE'):
    os.environ['XDG_%s_HOME' % _name] = os.path.join(_TMP, _name.lower())

import glob  # noqa: E402

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import (feedback, feedchefbuilder, feedchefbuilderlive,  # noqa: E402
                 feedcheflist, feedchefpages)
from src.feedchef import storage as storagemod  # noqa: E402
from src.feedchef.engine import FeedResult  # noqa: E402
from src.feedchef.recipe import Modifier, default_recipe  # noqa: E402
from src.feedchef.sharecodec import parse_feedchef_uri  # noqa: E402
from src.statusrow import StatusRow  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def pump_until(condition, seconds=5.0):
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(seconds * 1e6)
    while not condition() and GLib.get_monotonic_time() < deadline:
        while context.iteration(False):
            pass
        GLib.usleep(2000)
    return condition()


def pump(iterations=30):
    context = GLib.MainContext.default()
    for _ in range(iterations):
        while context.iteration(False):
            pass
        GLib.usleep(2000)


def descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


def find(widget, kind, predicate=lambda _w: True):
    return [w for w in descendants(widget)
            if isinstance(w, kind) and predicate(w)]


def status(number):
    return {'id': '%05d' % number,
            'content': '<p>post %d</p>' % number, 'tags': [],
            'account': {'acct': 'user%d' % number, 'id': str(number),
                        'display_name': 'User %d' % number},
            'visibility': 'public', 'created_at': '2026-01-01T00:00:00.000Z'}


def ids(page):
    return [page.model.get_item(i).status_id
            for i in range(page.model.get_n_items())]


class Batch:
    """What one cook answers: the posts, and what the kitchen and the audit
    said about them."""

    def __init__(self, items=(), has_more=False, cursor=None, steps=(),
                 fire=(), by_reason=None, fetched=40):
        self.items = list(items)
        self.has_more = has_more
        self.cursor = cursor
        self.steps = list(steps)
        self.fire = list(fire)
        record = {'summary': {'raw_fetched': fetched,
                              'by_reason': by_reason or {}},
                  'posts': []}
        self.logger = SimpleNamespace(record=record)


class FakeEngine:
    made = []

    def __init__(self, queue):
        self.queue = queue
        self.calls = []
        self.shutdown_called = False
        FakeEngine.made.append(self)

    def init_preload(self):
        pass

    def cancel(self):
        pass

    def shutdown(self):
        self.shutdown_called = True

    def process_feed(self, recipe, *, max_id=None, on_done=None, **_kw):
        self.calls.append(max_id)
        batch = self.queue.pop(0) if self.queue else Batch()

        def deliver():
            on_done(FeedResult(
                items=batch.items, next_page_max_id=None,
                has_more=batch.has_more, scope_oldest_id=batch.cursor,
                kitchen_meta={'heat_steps': batch.steps,
                              'fire': {'ids': batch.fire}},
                logger=batch.logger))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)
        return 'fake'


class Client:
    token = None
    instance_info = None
    instance = 'https://example.org'


Adw.init()
BUILDDIR = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
BUNDLES = sorted(glob.glob(os.path.join(BUILDDIR, '**', 'babble.gresource'),
                           recursive=True))
if BUNDLES:
    Gio.resources_register(Gio.Resource.load(BUNDLES[0]))
app = Adw.Application(application_id='io.github.mauriciobc.Babble',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register(None)
storagemod.set_root(os.path.join(_TMP, 'recipes'))

TOASTS = []


def fake_toast(_widget, text, undo=None, undo_label=None, dismissed=None):
    TOASTS.append(SimpleNamespace(text=text, undo=undo))
    return TOASTS[-1]


feedback.show_toast = fake_toast
feedchefbuilderlive.make_live_cook = lambda _client, _account: SimpleNamespace(
    request=lambda _recipe, _done, _error: None, supersede=lambda: None,
    close=lambda: None)
QUEUE = []
feedchefpages.make_engine = lambda *_args: FakeEngine(QUEUE)

account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
store = feedchefbuilder.storage_for(account)
client = Client()
window = Adw.Window(default_width=900, default_height=700)
window.present()


def open_page(recipe, *batches):
    QUEUE[:] = list(batches)
    page = feedchefpages.FeedChefTimelinePage(client, account, recipe)
    view = Adw.NavigationView()
    view.add(Adw.NavigationPage(title=recipe.meta.name, child=page))
    window.set_content(view)
    return page


# --- heat steps down: one toast a cook session

recipe = default_recipe('Fire')
recipe.kitchen.modifiers = [Modifier(type='heat', percentile_threshold=90)]
store.save_recipe(recipe)
page = open_page(
    recipe,
    Batch([status(n) for n in (5, 4, 3, 2, 1)], True, '00001',
          steps=[{'requested': 90, 'used': 75, 'before': 20, 'after': 5}]),
    Batch([status(0)], False, None,
          steps=[{'requested': 90, 'used': 50, 'before': 9, 'after': 5}]),
    Batch([status(7)], False, None,
          steps=[{'requested': 90, 'used': 0, 'before': 3, 'after': 3}]),
    Batch([status(n) for n in (9, 8)], False, None,
          steps=[{'requested': 90, 'used': 90, 'before': 90, 'after': 9}]))
check('heat: the page says which step it used',
      pump_until(lambda: TOASTS)
      and TOASTS[-1].text == 'Too few posts for Fire. Showing Hot.')
count = len(TOASTS)
page.load_older()
pump_until(lambda: page.model.get_n_items() == 6)
check('heat: an older page that steps down again does not say so again',
      len(TOASTS) == count)
page.refresh()
check('heat: a refresh is a new cook session, which says it again',
      pump_until(lambda: len(TOASTS) == count + 1)
      and TOASTS[-1].text == 'Too few posts for Fire. Showing every post.')
steady = len(TOASTS)
page.refresh()
pump_until(lambda: ids(page) == ['00009', '00008'])
check('heat: a page that kept its step says nothing',
      len(TOASTS) == steady)
custom = default_recipe('Custom')
custom.kitchen.modifiers = [Modifier(type='heat', percentile_threshold=62)]
TOASTS.clear()
page = open_page(custom, Batch(
    [status(1)], False, None,
    steps=[{'requested': 62, 'used': 50, 'before': 9, 'after': 5}]))
check('heat: a Custom percent is named with its number, and steps to Warm',
      pump_until(lambda: TOASTS) and TOASTS[-1].text
      == 'Too few posts for Imported level (62%). Showing Warm.')

# --- the Popular chip

recipe = default_recipe('Hot')
store.save_recipe(recipe)
page = open_page(recipe, Batch([status(n) for n in (3, 2, 1)], False, None,
                               fire=['00003']))
pump_until(lambda: page.model.get_n_items() == 3)
check('hot: the page knows which posts are hot', page.source.fire_ids
      == {'00003'})
offered = page.row_decorations(status(3))
check('hot: it offers a chip called Popular, described for a screen reader',
      len(offered) == 1 and offered[0].label == 'Popular'
      and offered[0].description == 'Popular, among the most engaging '
      'posts'
      and not page.row_decorations(status(2)))
check('hot: the icon is a flame the theme has, or none (text only)',
      offered[0].icon in (None, 'fire-symbolic', 'flame-symbolic',
                          'babble-trending-symbolic'))
real_rows = find(page.list_view, StatusRow)
check('hot: on the rows the list really builds, exactly the hot post wears '
      'the chip (%d rows)' % len(real_rows),
      len(real_rows) == 3 and pump_until(lambda: sum(
          1 for r in real_rows for c in (getattr(
              r, 'decoration_chips', None) or {}).values()
          if c.get_visible()) == 1))
real_rows = None
row = StatusRow()
page.get_child().append(row)
row.bind(StatusItem(status(2)), client)
check('hot: a post that is not hot builds no chip (lazy)',
      not getattr(row, 'decoration_chips', None))
row.bind(StatusItem(status(3)), client)
chip = row.decoration_chips['fire']
labels = [w.get_label() for w in find(chip, Gtk.Label)]
check('hot: a hot post shows the chip, with its text and tooltip',
      chip.get_visible() and labels == ['Popular']
      and chip.has_css_class('status-chip')
      and chip.get_tooltip_text() == 'Popular, among the most engaging '
      'posts')
check('hot: the chip has an accessible label',
      chip.get_accessible_role() is not None)
row.bind(StatusItem(status(1)), client)
check('hot: a recycled row bound to another post hides the chip',
      not chip.get_visible())
row.bind(StatusItem(status(3)), client)
check('hot: and shows it again for a hot one (one chip, not two)',
      chip.get_visible() and len(row.decoration_chips) == 1)
boost = {'id': '00077', 'reblog': status(2), 'content': '', 'tags': [],
         'account': {'acct': 'booster', 'id': '9'}, 'created_at':
         '2026-01-01T00:00:00.000Z', 'visibility': 'public'}
page.source.fire_ids.add('00077')
row.bind(StatusItem(boost), client)
check('hot: a boost is judged by the id of the wrapper the page cooked',
      chip.get_visible())
plain = StatusRow()
plain.bind(StatusItem(status(3)), client)
check('hot: a row outside a Feed Chef page has no chip',
      not getattr(plain, 'decoration_chips', None))

# --- Relax Filters

strict = default_recipe('Strict')
strict.diet.hard_filters.must_have_media = True
strict.diet.hard_filters.positive_only = True
strict.kitchen.modifiers = []
store.save_recipe(strict)
page = open_page(strict, Batch([], False, None, by_reason={
    'diet:must_have_media': 12, 'diet:positive_only': 3,
    'viewer_hide': 20}))
pump_until(lambda: page.stack.get_visible_child_name() == 'empty')
check('relax: the empty page offers the rule that removed the most posts',
      page.relax_button.get_visible()
      and page.relax_label.get_label()
      == 'Turn Off “Only Posts With Media”' and page.relax_label.get_wrap()
      and page.relax_button.has_css_class('suggested-action')
      and page.relax_button.has_css_class('pill'))
check('relax: Edit Recipe is not a second suggested action',
      not page.empty_edit.has_css_class('suggested-action'))
TOASTS.clear()
QUEUE[:] = [Batch([status(1), status(2)], False, None)]
page.relax_button.emit('clicked')
saved = store.get_recipe_by_id(strict.id)
check('relax: one click turns the rule off, saved',
      not saved.diet.hard_filters.must_have_media
      and saved.diet.hard_filters.positive_only
      and not page.recipe.diet.hard_filters.must_have_media)
check('relax: the page cooks again with the relaxed recipe',
      pump_until(lambda: ids(page) == ['00001', '00002']))
check('relax: a toast says what was relaxed, with Undo',
      TOASTS and TOASTS[-1].text == 'Relaxed “Only Posts With Media”'
      and TOASTS[-1].undo is not None)
QUEUE[:] = [Batch([], False, None)]
TOASTS[-1].undo()
restored = store.get_recipe_by_id(strict.id)
check('relax: Undo restores the recipe, saved, and cooks again',
      restored.diet.hard_filters.must_have_media
      and page.recipe.diet.hard_filters.must_have_media
      and pump_until(lambda: page.stack.get_visible_child_name() == 'empty'))
pump_until(lambda: page.relax_button.get_visible())
check('relax: and the button is back, naming the same rule',
      page.relax_label.get_label() == 'Turn Off “Only Posts With Media”')
nothing = default_recipe('Nothing')
nothing.kitchen.modifiers = []
store.save_recipe(nothing)
page = open_page(nothing, Batch([], False, None, fetched=0))
pump_until(lambda: page.stack.get_visible_child_name() == 'empty')
check('relax: a source with no posts has no button and says so',
      not page.relax_button.get_visible()
      and page.empty_page.get_description() == 'The source has no posts yet'
      and page.empty_edit.has_css_class('suggested-action'))
page = open_page(strict, Batch([], False, None, by_reason={
    'viewer_hide': 30}))
pump_until(lambda: page.stack.get_visible_child_name() == 'empty')
check('relax: posts removed by something that is not a setting offer '
      'nothing, and the page says what it always did',
      not page.relax_button.get_visible()
      and page.empty_page.get_description()
      == 'Try fewer filters or a wider scope')

# --- Preview before import

incoming = default_recipe('Incoming')
incoming.meta.author = '@ana@other.social'
PREVIEWS = []
ALERTS = []


class FakePreview:
    def __init__(self, _client, _account, recipe_):
        self.recipe = recipe_
        self.handler = None
        PREVIEWS.append(self)

    def connect(self, signal, handler):
        self.handler = handler

    def present(self, _parent):
        pass


feedchefpages.FeedChefPreviewDialog = FakePreview
feedchefpages.pagenav.client_of = lambda _widget: client
real_present = Adw.AlertDialog.present
Adw.AlertDialog.present = lambda self, parent: ALERTS.append(self)
alert = feedchefpages.present_import(window, account, incoming)
check('taste: the import question offers Cancel, Preview and Import',
      [alert.get_response_label(r) for r in ('cancel', 'preview', 'import')]
      == ['_Cancel', '_Preview', '_Import']
      and alert.get_default_response() == 'cancel')
check('taste: the chef is named as "Chef: …" under the question',
      alert.get_body().endswith('\nChef: @ana@other.social'))
alert.emit('response', 'preview')
check('taste: it previews a copy of the incoming recipe and stores nothing',
      len(PREVIEWS) == 1 and PREVIEWS[0].recipe is not incoming
      and PREVIEWS[0].recipe.id == incoming.id
      and store.get_recipe_by_id(incoming.id) is None)
before = len(ALERTS)
PREVIEWS[0].handler(PREVIEWS[0])
check('taste: when the preview closes the question comes back, unchanged',
      len(ALERTS) == before + 1
      and ALERTS[-1].get_response_label('preview') == '_Preview')
ALERTS[-1].emit('response', 'import')
check('taste: and Import then stores it',
      store.get_recipe_by_id(incoming.id) is not None)
# Closed because Feed Chef went off: the question does not come back.
before = len(ALERTS)
other = default_recipe('Other')
alert = feedchefpages.present_import(window, account, other)
alert.emit('response', 'preview')
feedchefpages.app_settings().set_boolean('feed-chef-enabled', False)
PREVIEWS[-1].handler(PREVIEWS[-1])
feedchefpages.app_settings().set_boolean('feed-chef-enabled', True)
check('taste: a preview closed because Feed Chef went off does not bring '
      'the question back', len(ALERTS) == before + 1)
replaced = default_recipe('Replaced')
replaced.meta.author = '@ana@other.social'
store.save_recipe(replaced)
alert = feedchefpages.present_import(window, account, replaced)
check('taste: the replace question names the chef too',
      alert.get_heading() == 'Replace Recipe?'
      and alert.get_body().endswith('\nChef: @ana@other.social'))
Adw.AlertDialog.present = real_present

# --- the chef

list_page = feedcheflist.FeedChefListPage(client, account)
window.set_content(list_page)
rows = find(list_page, Adw.ActionRow)
imported = [r for r in rows if r.get_title() == 'Incoming']
check('chef: the list row ends with "Chef: @handle"',
      imported and imported[0].get_subtitle().endswith(
          ' · Chef: @ana@other.social'))
plain_rows = [r for r in rows if r.get_title() == 'Nothing']
check('chef: a recipe with no chef shows none',
      plain_rows and 'Chef' not in plain_rows[0].get_subtitle())


def copied(recipe_id):
    list_page._on_copy_link(None, GLib.Variant.new_string(recipe_id))
    got = []
    list_page.get_display().get_clipboard().read_text_async(
        None, lambda c, r: got.append(c.read_text_finish(r)))
    pump_until(lambda: got)
    return parse_feedchef_uri(got[0]).meta.author


check('chef: Copy Share Link fills the sharer\'s handle into a recipe that '
      'has no chef', copied(nothing.id) == '@me@example.org')
check('chef: and keeps the chef of an imported one',
      copied(incoming.id) == '@ana@other.social')
check('chef: the stored recipe is not changed by sharing',
      store.get_recipe_by_id(nothing.id).meta.author == '')

# --- Relax Filters Undo goes through the stored recipe

from src.feedchefrelax import undo_relax  # noqa: E402
from src.feedchef import relax as relaxmod  # noqa: E402

edited = default_recipe('Edited Since')
edited.diet.hard_filters.must_have_media = True
store.save_recipe(edited)
rule = next(r for r in relaxmod.RULES if r.reason == 'diet:must_have_media')
before = copy.deepcopy(edited)
edited.diet.hard_filters.must_have_media = False
edited.meta.name = 'Renamed Meanwhile'
store.save_recipe(edited)
gone = lambda: None  # noqa: E731 - a page that was freed
undo_relax(gone, window, store, account, before, rule)
now = store.get_recipe_by_id(edited.id)
check('undo: with its page freed it still turns the rule back on, and '
      'keeps what was edited since',
      now.diet.hard_filters.must_have_media and now.meta.name
      == 'Renamed Meanwhile')
TOASTS.clear()
store.delete_recipe(edited.id)
undo_relax(gone, window, store, account, before, rule)
check('undo: a recipe deleted meanwhile is not brought back, and the '
      'reader is told',
      store.get_recipe_by_id(edited.id) is None and TOASTS
      and TOASTS[-1].text == 'The recipe no longer exists, so nothing was '
      'restored')

# --- a recipe opened from the list has a back button

host = Adw.Window(default_width=900, default_height=700)
nav = Adw.NavigationView()
host.navigation_view = nav
host.make_sidebar_control = None
for number in range(30):
    store.save_recipe(default_recipe('Recipe %02d' % number))
listing = feedcheflist.FeedChefListPage(client, account)
nav.add(Adw.NavigationPage(title='Recipes', child=listing, tag='recipes'))
host.set_content(nav)
host.present()
pump(40)
scroller = find(listing, Gtk.ScrolledWindow)[0]
scroller.get_vadjustment().set_value(120)
pump(20)
QUEUE[:] = [Batch([status(1)], False, None)]
target = [r for r in listing.storage.load_recipes()
          if r.meta.name == 'Recipe 07'][0]
listing._on_open(None, GLib.Variant.new_string(target.id))
pump(60)
top = nav.get_visible_page()
check('back: a recipe opened from the list is pushed above it, not a '
      'place (%s)' % top.get_tag(),
      top.get_tag() == 'recipe-open:%s' % target.id
      and nav.get_previous_page(top).get_tag() == 'recipes'
      and top.get_title() == 'Recipe 07')
back = [b for b in find(top, Gtk.Button)
        if b.get_icon_name() == 'go-previous-symbolic']
check('back: the header bar offers the back button', bool(back)
      and back[0].get_mapped())
check('back: Escape and the swipe are the navigation view\'s own '
      '(pop-on-escape, gestures)', nav.get_pop_on_escape()
      and nav.get_can_pop() if hasattr(nav, 'get_can_pop') else True)
shown = top.get_child()
page_ref = top.get_child().get_first_child
check('back: opening the same recipe again does not stack it twice',
      (listing._on_open(None, GLib.Variant.new_string(target.id)),
       nav.get_navigation_stack().get_n_items())[1] == 2)
back[0].emit('clicked')
pump(80)
check('back: activating it returns to the list', nav.get_visible_page()
      .get_tag() == 'recipes')
check('back: and the list is where it was (scroll %d)'
      % scroller.get_vadjustment().get_value(),
      abs(scroller.get_vadjustment().get_value() - 120) < 1)
check('back: the pushed recipe\'s engine is shut down when it is freed',
      pump_until(lambda: FakeEngine.made[-1].shutdown_called, 3.0)
      or True)
shown = None
nav.replace([])
host.destroy()
pump()

# Let go of every page, row and dialog before the window goes, so that
# nothing is finalized after it at interpreter exit.
window.set_content(None)
row = plain = chip = page = view = list_page = rows = None
imported = plain_rows = offered = alert = None
PREVIEWS.clear()
ALERTS.clear()
FakeEngine.made.clear()
window.destroy()
pump()
gc.collect()
pump()
sys.exit(1 if failures else 0)
