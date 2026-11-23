#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's UI over a fake engine: the timeline is a TimelinePage in the
centred column, kitchen order is kept, More Like This lives in the post
menu and drives the focus banner, a refresh cooks again, the recipe list
and the builder behave, and a dropped page is freed. Exits 77 (skip)
without a display."""

import os
import tempfile

# Never the user's real dconf, data or state.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
_TMP = tempfile.mkdtemp(prefix='babble-feedchef-ui-')
for _name in ('DATA', 'STATE', 'CACHE'):
    os.environ['XDG_%s_HOME' % _name] = os.path.join(_TMP, _name.lower())

import copy  # noqa: E402
import gc  # noqa: E402
import sys  # noqa: E402
from types import SimpleNamespace  # noqa: E402

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
                 feedchefpages)
from src.feedchefsource import MAX_EMPTY_HOPS  # noqa: E402
from src.feedchef import storage as storagemod  # noqa: E402
from src.feedchef.engine import FeedResult  # noqa: E402
from src.feedchef.recipe import Modifier, default_recipe  # noqa: E402
from src.statusmenu import StatusMenuButton  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402
from src.timelinepage import TimelinePage  # noqa: E402

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


def freed(ref, attempts=5):
    for _attempt in range(attempts):
        gc.collect()
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


def status(number, tags=(), text='post'):
    return {'id': '%05d' % number,
            'content': '<p>%s %s</p>' % (text, ' '.join(
                '#%s' % tag for tag in tags)),
            'tags': [{'name': tag} for tag in tags],
            'account': {'acct': 'user%d' % number, 'id': str(number)},
            'created_at': '2026-01-01T00:00:00.000Z'}


def ids(page):
    return [page.model.get_item(i).status_id
            for i in range(page.model.get_n_items())]


class FakeEngine:
    """Answers each cook with the next queued batch, on the main loop."""

    made = []

    def __init__(self, queue=None):
        self.calls = []
        self.queue = list(queue or [])
        self.fail = False
        self.cancelled = 0
        self.shutdown_called = False
        FakeEngine.made.append(self)

    def init_preload(self):
        pass

    def cancel(self):
        self.cancelled += 1

    def shutdown(self):
        self.shutdown_called = True

    def process_feed(self, recipe, *, max_id=None, count=80, on_stage=None,
                     on_done=None, on_error=None, author_counts=None,
                     hide=None, cook_id=None, page_index=0, hop=0,
                     instance_counts=None):
        self.calls.append(max_id)
        items, has_more, cursor = (self.queue.pop(0) if self.queue
                                   else ([], False, None))
        fail = self.fail
        self.fail = False

        def deliver():
            if fail:
                on_error(RuntimeError('boom'))
            else:
                on_done(FeedResult(items=items, next_page_max_id=None,
                                   has_more=has_more,
                                   scope_oldest_id=cursor))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)
        return 'fake'


class Client:
    token = None
    instance_info = None
    instance = 'https://example.org'


Adw.init()
storagemod.set_root(os.path.join(_TMP, 'recipes'))

# Toasts are recorded, not shown: an Undo toast is answered by the test.
TOASTS = []


def fake_toast(_widget, text, undo=None, undo_label=None, dismissed=None):
    TOASTS.append(SimpleNamespace(text=text, undo=undo,
                                  dismissed=dismissed))
    return TOASTS[-1]


feedback.show_toast = fake_toast
feedchefbuilderlive.make_live_cook = lambda _client, _account: SimpleNamespace(
    request=lambda _recipe, _done, _error: None, supersede=lambda: None,
    close=lambda: None)
# The account's lists are answered by the test, not by a server.
LIST_REQUESTS = []
feedchefbuilder.pages.call_api = (
    lambda _widget, _client, _start, on_done, _heading, finish=None:
    LIST_REQUESTS.append(on_done))
FIRST_BATCHES = []
feedchefpages.make_engine = lambda *_args: FakeEngine(FIRST_BATCHES)

account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
store = feedchefbuilder.storage_for(account)
client = Client()
window = Gtk.Window(default_width=900, default_height=700)
window.present()

# --- the recipe list: empty state, then rows ---
list_page = feedchefpages.FeedChefListPage(client, account)
window.set_child(list_page)
check('list: no recipes shows the empty state',
      list_page.stack.get_visible_child_name() == 'empty')
empty = list_page.stack.get_child_by_name('empty')
check('list: empty state is a status page with the Feed Chef icon',
      isinstance(empty, Adw.StatusPage)
      and empty.get_icon_name() == 'babble-feed-chef-symbolic'
      and empty.get_title() == 'No Recipes Yet')
recipe = default_recipe('Linux')
recipe.pantry.hashtags_include = ['linux', 'python', 'gnome', 'extra']
store.save_recipe(recipe)
other = default_recipe('Local')
other.pantry.scope = 'local'
store.save_recipe(other)
list_page.reload()
rows = find(list_page, Adw.ActionRow)
check('list: a row per recipe, activatable',
      len(rows) == 2 and all(r.get_activatable() for r in rows))
check('list: subtitle is the recipe in one sentence',
      rows[0].get_subtitle() == 'Posts from Home about #linux #python '
      '#gnome and 1 more, up to 3 posts per person, most engaging first'
      and rows[1].get_subtitle() == 'Posts from the local timeline, up to '
      '3 posts per person, most engaging first')
check('list: a row opens its recipe by action',
      rows[0].get_action_name() == 'recipe.open')
menus = find(rows[0], Gtk.MenuButton)
sections = []
if menus:
    model = menus[0].get_menu_model()
    for number in range(model.get_n_items()):
        inner = model.get_item_link(number, 'section')
        sections.append([
            inner.get_item_attribute_value(i, 'label', None).get_string()
            for i in range(inner.get_n_items())])
check('list: options menu has Edit, Pin, Copy Share Link, then Delete '
      'in a section of its own',
      sections == [['_Edit', '_Pin to Sidebar', '_Copy Share Link'],
                   ['_Delete']])
check('list: options button names its recipe in tooltip and label',
      bool(menus)
      and menus[0].get_tooltip_text() == 'Options for “Linux”')

# Delete takes the row away at once and commits when the toast is gone.
rows_before = len(store.load_recipes())
list_page.activate_action('recipe.delete',
                          GLib.Variant.new_string(other.id))
undo_toast = TOASTS[-1]
check('delete: the row is hidden at once, nothing is deleted yet',
      undo_toast.text == 'Recipe deleted' and undo_toast.undo is not None
      and not list_page._row_by_id[other.id].get_visible()
      and store.get_recipe_by_id(other.id) is not None)
undo_toast.undo()
check('delete: Undo brings the row back and keeps the recipe',
      list_page._row_by_id[other.id].get_visible()
      and store.get_recipe_by_id(other.id) is not None)
list_page.activate_action('recipe.delete',
                          GLib.Variant.new_string(other.id))
TOASTS[-1].dismissed()
check('delete: the toast going away deletes the recipe',
      store.get_recipe_by_id(other.id) is None
      and len(store.load_recipes()) == rows_before - 1
      and len(list_page._rows) == rows_before - 1)
store.save_recipe(other)
list_page.reload()

# --- the builder: Cancel discards, Save stores, a new recipe waits ---
saved = []


def open_builder(recipe, is_new, parent=None):
    dialog = feedchefbuilder.FeedChefBuilderDialog(
        client, account, recipe, is_new=is_new)
    dialog.connect('saved', lambda _d: saved.append(True))
    dialog.present(parent or window)
    pump()
    return dialog


def header_button(dialog, label):
    return find(dialog.header, Gtk.Button,
                lambda b: b.get_label() == label)[0]


fresh = default_recipe('Brand New')
dialog = open_builder(fresh, True)
check('builder: new recipe is titled New Recipe',
      dialog.get_title() == 'New Recipe')
check('builder: nothing is stored before Save',
      store.get_recipe_by_id(fresh.id) is None)
closed = []
dialog.connect('closed', lambda _d: closed.append(True))
check('builder: untouched, it can close at once', dialog.get_can_close())
dialog.name_row.set_text('Edited')
check('builder: an edit makes it ask before closing',
      not dialog.get_can_close())
header_button(dialog, '_Cancel').emit('clicked')
pump()
alert = dialog.discard_alert
check('builder: Cancel with edits asks "Discard Changes?"',
      alert is not None and alert.get_heading() == 'Discard Changes?'
      and alert.get_body()
      == 'Your changes to this recipe will be lost.'
      and alert.get_default_response() == 'keep'
      and alert.get_close_response() == 'keep'
      and alert.get_response_label('keep') == '_Keep Editing'
      and alert.get_response_label('discard') == '_Discard'
      and alert.get_response_appearance('discard')
      == Adw.ResponseAppearance.DESTRUCTIVE and not closed)
alert.emit('response', 'keep')
alert.force_close()
pump()
check('builder: Keep Editing leaves the dialog open and unsaved',
      not closed and dialog.discard_alert is None
      and store.get_recipe_by_id(fresh.id) is None)
dialog.name_row.set_text('Brand New')
check('builder: undoing the edit by hand makes it clean again',
      dialog.get_can_close())
dialog.name_row.set_text('Edited')
dialog.close()
pump()
alert = dialog.discard_alert
alert.emit('response', 'discard')
alert.force_close()
pump_until(lambda: not dialog.get_mapped())
check('builder: Discard closes without saving',
      not dialog.get_mapped()
      and store.get_recipe_by_id(fresh.id) is None and not saved)
dialog = open_builder(fresh, True)
closed.clear()
dialog.connect('closed', lambda _d: closed.append(True))
header_button(dialog, '_Cancel').emit('clicked')
pump()
check('builder: Cancel without edits just closes',
      closed and dialog.discard_alert is None)
check('builder: Cancel leaves the caller\'s recipe alone',
      fresh.meta.name == 'Brand New')

kept = default_recipe('Keeps Inert')
kept.kitchen.modifiers = [
    Modifier(type='connection'),
    Modifier(type='diversity', max_per_cluster=3,
             diversity_dimensions=['author']),
    Modifier(type='freshness', decay_minutes=100),
]
store.save_recipe(kept)
dialog = open_builder(kept, False)
check('builder: edit is titled Edit Recipe',
      dialog.get_title() == 'Edit Recipe')
inert_rows = find(dialog.page, Adw.ActionRow, lambda r: r.get_title()
                  == 'Some Ingredients Are Not Supported Yet')
check('builder: an unsupported ingredient is named in plain words, and '
      'sentiment and similarity grouping, which are active now, are not',
      len(inert_rows) == 1 and 'connections' in inert_rows[0].get_subtitle()
      and 'sentiment' not in inert_rows[0].get_subtitle()
      and 'similarity' not in inert_rows[0].get_subtitle()
      and 'positive' not in inert_rows[0].get_subtitle())
dialog.name_row.set_text('Renamed')
dialog.min_length.set_value(120)
dialog.reach.set_active(True)
heat = [e for e in dialog.editors if e.kind == 'heat'][0]
heat.row.set_enable_expansion(True)
diversity = [e for e in dialog.editors if e.kind == 'diversity'][0]
diversity.row.set_enable_expansion(False)
header_button(dialog, '_Save').emit('clicked')
pump()
after = store.get_recipe_by_id(kept.id)
check('builder: Save stores and announces', saved and after is not None
      and after.meta.name == 'Renamed')
check('builder: minimum length and reach are written',
      after.diet.hard_filters.min_content_length == 120
      and after.pantry.network_reach.enabled
      and after.pantry.network_reach.max_degrees == 1)
types = [m.type for m in after.kitchen.modifiers]
check('builder: order kept, switched-off removed, new one appended',
      types == ['connection', 'freshness', 'heat'])
fresh_mod = after.kitchen.modifiers[1]
check('builder: an untouched value is not rewritten',
      fresh_mod.decay_minutes == 100
      and after.kitchen.modifiers[0].type == 'connection')
check('builder: a new heat modifier defaults to 50 percent',
      after.kitchen.modifiers[2].percentile_threshold == 50)

dialog = open_builder(fresh, True)
header_button(dialog, '_Save').emit('clicked')
pump()
check('builder: a new recipe is stored by Save',
      store.get_recipe_by_id(fresh.id) is not None)
for stored in store.load_recipes():
    store.delete_recipe(stored.id)


# Save waits for a list while the scope is List.
listed = default_recipe('Listed')
listed.pantry.scope = 'list'
LIST_REQUESTS.clear()
dialog = open_builder(listed, True)
save = header_button(dialog, '_Save')
check('builder: the account\'s lists are asked for', len(LIST_REQUESTS) == 1)
check('builder: Save is insensitive with scope List and no list',
      not save.get_sensitive())
LIST_REQUESTS[0]([{'id': '7', 'title': 'Friends'},
                  {'id': '8', 'title': 'Work'}])
pump()
check('builder: the row shows its first list, Save follows what is shown',
      save.get_sensitive() and dialog.list_row.get_sensitive()
      and dialog.list_row.get_selected() == 0 and dialog.get_can_close())
dialog.scope.set_selected(0)
check('builder: another scope needs no list', save.get_sensitive())
dialog.scope.set_selected(3)
dialog.list_row.set_selected(1)
check('builder: choosing a list is an edit', not dialog.get_can_close())
save.emit('clicked')
pump()
check('builder: the chosen list is stored',
      store.get_recipe_by_id(listed.id).pantry.list_id == '8')

none_yet = default_recipe('No Lists')
none_yet.pantry.scope = 'list'
LIST_REQUESTS.clear()
dialog = open_builder(none_yet, True)
LIST_REQUESTS[0]([])
pump()
check('builder: an account with no lists says so and Save stays off',
      dialog.list_row.get_subtitle() == 'Create a list first'
      and not dialog.list_row.get_sensitive()
      and not header_button(dialog, '_Save').get_sensitive())
dialog.force_close()
pump()

# A recipe that already has a list is not an edit until the reader makes one.
has_list = default_recipe('Has List')
has_list.pantry.scope = 'list'
has_list.pantry.list_id = '8'
LIST_REQUESTS.clear()
dialog = open_builder(has_list, False)
check('builder: a recipe with a list can be saved before the lists arrive',
      header_button(dialog, '_Save').get_sensitive()
      and dialog.get_can_close())
LIST_REQUESTS[0]([{'id': '7', 'title': 'Friends'},
                  {'id': '8', 'title': 'Work'}])
pump()
check('builder: its list is selected and loading lists is not an edit',
      dialog.list_row.get_selected() == 1 and dialog.get_can_close())
dialog.force_close()
pump()

# Enter saves, access keys are unique.
dialog = open_builder(default_recipe('Enter'), True)
check('builder: Enter in the name saves (default widget, activates)',
      dialog.get_default_widget() is header_button(dialog, '_Save')
      and dialog.name_row.get_activates_default())
share_rows = [r for r in find(dialog.page, Adw.PreferencesRow)
              if r.get_title() in ('_Preview Recipe', 'S_hare Recipe')]
check('builder: the bottom rows use access keys no header button uses',
      [r.get_title() for r in share_rows]
      == ['_Preview Recipe', 'S_hare Recipe'])
orders = dialog.order.get_model()
check('builder: Order items are sentence case',
      [orders.get_string(i) for i in range(orders.get_n_items())]
      == ['Most engaging', 'Newest first'])
diet = [g for g in find(dialog.page, Adw.PreferencesGroup)
        if g.get_title() == 'Diet']
check('builder: Diet says how to separate words',
      diet and diet[0].get_description() == 'Separate words with spaces. '
      'Whole words only, in the post and its content warning, ignoring '
      'case.')
dialog.force_close()
pump()

# --- the timeline ---
recipe = default_recipe('Cook')
store.save_recipe(recipe)
kitchen_order = [50, 10, 40, 20]
FIRST_BATCHES.append(([status(n, tags=['python'] if n == 40 else ())
                       for n in kitchen_order], True, '00777'))
page = feedchefpages.FeedChefTimelinePage(client, account, recipe)
engine = FakeEngine.made[-1]
navpage = Adw.NavigationPage(title='Cook', child=page)
navview = Adw.NavigationView()
navview.add(navpage)
window.set_child(navview)
check('timeline: it is a TimelinePage', isinstance(page, TimelinePage))
check('timeline: the list sits in a centred clamp',
      isinstance(page.list_view.get_parent(), Adw.ClampScrollable))
check('timeline: first page shows the cooked posts',
      pump_until(lambda: page.model.get_n_items() == 4))
check('timeline: kitchen order is kept, not sorted by id',
      ids(page) == ['00050', '00010', '00040', '00020'])
check('timeline: one cook was started for the first page',
      engine.calls == [None])
check('timeline: the banner is hidden without a focus',
      not page.banner.get_revealed())

# Older page: the engine's own cursor, then the end.
engine.queue = [([status(5)], False, None)]
page.load_older()
check('timeline: older page appended',
      pump_until(lambda: page.model.get_n_items() == 5))
check('timeline: the older page asked with the scope cursor',
      engine.calls == [None, '00777'])
page._release_bottom()
page.load_older()
pump()
check('timeline: no more means no further cook',
      engine.calls == [None, '00777'] and page.model.exhausted)

# More Like This in the post menu.
button = StatusMenuButton()
button.bind(StatusItem(status(40, tags=['python'])), client)
page.get_child().append(button)
button.prepare()
menu = button.get_menu_model()
entries = {}
for section in range(menu.get_n_items()):
    inner = menu.get_item_link(section, 'section')
    for index in range(inner.get_n_items() if inner else 0):
        label = inner.get_item_attribute_value(index, 'label', None)
        action = inner.get_item_attribute_value(index, 'action', None)
        entries[label.get_string()] = action.get_string()
check('menu: More Like This is in the post menu',
      entries.get('More _Like This', '').startswith('page.'))
check('menu: the post buttons are gone from the rows',
      not find(page.list_view, Gtk.Button,
               lambda b: b.get_label() == 'More like this'))
engine.queue = [([status(99), status(1)], False, None)]
before = len(engine.calls)
button.activate_action(entries['More _Like This'], None)
check('menu: it sets the focus from that post',
      page.recipe.kitchen.active_focus_hashtags == ['python'])
check('menu: the banner shows the focus',
      page.banner.get_revealed() and '#python' in page.banner.get_title())
check('menu: the focus is stored',
      store.get_recipe_by_id(recipe.id).kitchen.active_focus_hashtags
      == ['python'])
check('menu: the timeline was cooked again',
      pump_until(lambda: ids(page) == ['00099', '00001']))
check('menu: refresh replaced the list, nothing was prepended',
      len(engine.calls) == before + 1 and engine.calls[-1] is None
      and page.model.get_n_items() == 2)

# Clear.
engine.queue = [([status(2)], False, None)]
page.banner.emit('button-clicked')
check('banner: the button says what it clears',
      page.banner.get_button_label() == 'Clear Focus')
check('banner: Clear hides it and clears the focus',
      not page.banner.get_revealed()
      and page.recipe.kitchen.active_focus_hashtags is None)
check('banner: Clear cooks again',
      pump_until(lambda: ids(page) == ['00002']))

# Refresh, as the window's reload action does it.
engine.queue = [([status(8), status(7)], False, None)]
count = len(engine.calls)
page.refresh()
check('refresh: the engine is asked again and the list is replaced',
      pump_until(lambda: ids(page) == ['00008', '00007'])
      and len(engine.calls) == count + 1)

# A reader's refresh fades the focus (focus_decay); setting the focus and
# the recipe's own changes do not.
kit = page.recipe.kitchen
kit.active_focus_keywords = ['coffee', 'tea']
kit.active_focus_hashtags = ['python']
kit.focus_decay = 0.5
page._update_focus_ui()
engine.queue = [([status(3)], False, None)]
count = len(engine.calls)
stopped = engine.cancelled
page.refresh()
check('decay: the cook in flight is cancelled before the focus changes',
      engine.cancelled > stopped)
check('decay: a refresh multiplies the focus weights and stores the recipe',
      kit.active_focus_weights == [0.5, 0.5]
      and store.get_recipe_by_id(recipe.id).kitchen.active_focus_weights
      == [0.5, 0.5] and pump_until(lambda: len(engine.calls) == count + 1))
for _step in range(3):
    engine.queue = [([status(3)], False, None)]
    page.refresh()
    pump()
check('decay: a weight under 0.1 goes, with none left the focus clears and '
      'the banner follows',
      kit.active_focus_keywords is None and kit.active_focus_hashtags is None
      and not page.banner.get_revealed()
      and store.get_recipe_by_id(recipe.id).kitchen.active_focus_keywords
      is None)
kit.focus_decay = 1.0
kit.active_focus_keywords = ['coffee']
engine.queue = [([status(3)], False, None)]
page.refresh()
check('decay: the default of 1.0 changes nothing',
      kit.active_focus_keywords == ['coffee']
      and kit.active_focus_weights is None)
kit.active_focus_keywords = None
pump()

# Errors show the error page; an empty cook shows the empty page.
engine.fail = True
page.refresh()
check('error: a failed cook shows the error page',
      pump_until(lambda: page.stack.get_visible_child_name() == 'error'))
engine.queue = [([], False, None)]
page.refresh()
check('empty: nothing cooked shows the empty page',
      pump_until(lambda: page.stack.get_visible_child_name() == 'empty'))
check('error: it is titled for the recipe',
      page.error_page.get_title() == 'Could Not Load This Recipe')
check('empty: its text and Edit Recipe button',
      page.empty_page.get_title() == 'No Matching Posts'
      and not page.more_button.get_visible()
      and page.empty_page.get_icon_name() == 'babble-feed-chef-symbolic'
      and bool(find(page.empty_page, Gtk.Button,
                    lambda b: b.get_label() == 'Edit Recipe')))
check('loading: the loading page says what is cooking',
      page.loading_page.get_title() == 'Cooking Your Timeline')

# --- the empty hops: a pause is not the end ---
engine.queue = [([], True, 'c%d' % n) for n in range(MAX_EMPTY_HOPS + 1)]
count = len(engine.calls)
page.refresh()
check('hops: empty batches are hopped over, then the page pauses',
      pump_until(lambda: page.stack.get_visible_child_name() == 'empty')
      and len(engine.calls) == count + MAX_EMPTY_HOPS + 1)
check('hops: the pause is not shown as the end',
      page.more_button.get_visible()
      and page.empty_page.get_title() == 'No Matching Posts Yet')
engine.queue = [([status(61)], True, 'e1')]
page.more_button.emit('clicked')
check('hops: Load Older Posts continues from the scope cursor',
      pump_until(lambda: ids(page) == ['00061'])
      and engine.calls[-1] == 'c%d' % MAX_EMPTY_HOPS)
engine.queue = [([], True, 'd%d' % n) for n in range(MAX_EMPTY_HOPS + 1)]
count = len(engine.calls)
page._release_bottom()
page.load_older()
check('hops: an older page that hops out is not the end of the list',
      pump_until(lambda: len(engine.calls) == count + MAX_EMPTY_HOPS + 1)
      and pump_until(lambda: not page._loading_older)
      and not page.model.exhausted and page.source.stalled)
engine.queue = [([], False, None)]
page._release_bottom()
page.load_older()
check('hops: the real end still ends it',
      pump_until(lambda: page.model.exhausted))

# --- closing the source does not show a cancelled cook as an error ---
engine.queue = [([status(3)], False, None)]
page.refresh()
page.use_recipe(page.recipe)
pump()
check('close: a cook stopped for a new recipe shows no error page',
      page.stack.get_visible_child_name() != 'error'
      and 'ancel' not in page.error_page.get_description())
dying = feedchefpages.FeedChefTimelinePage(client, account, recipe)
dying_ref = dying.weak_ref()
dying = None
pump()
check('close: a page disposed mid-cook is freed without a critical',
      freed(dying_ref))

# --- the focus banner names three terms and keeps the rest in its tooltip
kitchen = page.recipe.kitchen
kitchen.active_focus_hashtags = ['a', 'b', 'c', 'd', 'e']
kitchen.active_focus_keywords = ['kw']
page._update_focus_ui()
check('banner: at most three terms, hashtags first, then "and N more"',
      page.banner.get_title() == 'Showing more like #a, #b, #c and 3 more')
check('banner: the tooltip keeps the whole list',
      page.banner.get_tooltip_text() == '#a, #b, #c, #d, #e, kw')
kitchen.active_focus_hashtags = ['a']
page._update_focus_ui()
check('banner: a short list is shown whole',
      page.banner.get_title() == 'Showing more like #a, kw')
kitchen.active_focus_hashtags = None
kitchen.active_focus_keywords = None
page._update_focus_ui()
check('banner: no focus hides it', not page.banner.get_revealed())

# --- the header's Edit Recipe button ---
check('header: Edit Recipe is an icon button with tooltip and name',
      page.header_widgets == [page.report_button, page.edit_button]
      and page.edit_button.get_icon_name() == 'document-edit-symbolic'
      and page.edit_button.get_tooltip_text() == 'Edit Recipe')
made = []
Builder = feedchefpages.FeedChefBuilderDialog
feedchefpages.FeedChefBuilderDialog = (
    lambda *args, **kw: made.append(Builder(*args, **kw)) or made[-1])
old_engine = engine
page.edit_button.emit('clicked')
pump()
check('header: it opens the builder on this recipe',
      len(made) == 1 and made[0].recipe.id == page.recipe.id)
made[0].name_row.set_text('Cooked Again')
header_button(made[0], '_Save').emit('clicked')
pump()
engine = FakeEngine.made[-1]
check('header: a save cooks the edited recipe with a new engine',
      engine is not old_engine and old_engine.shutdown_called
      and page.recipe.meta.name == 'Cooked Again'
      and pump_until(lambda: page.model.get_n_items() == 4))
check('header: the place title follows the rename',
      navpage.get_title() == 'Cooked Again')

# --- an edit or an import reaches the places that show the recipe ---
refreshed = []
window.navigation_view = object()
window.account = account
window.navigation = SimpleNamespace(children={
    'feed-chef': list_page, 'recipe:%s' % page.recipe.id: page})
window.sidebar_host = SimpleNamespace(
    refresh=lambda: refreshed.append(True))
settings = feedchefpages.app_settings()
settings.set_strv(feedchefpages.PINNED_KEY, [
    feedchefpages._pin_entry(account, page.recipe.id)])
incoming = copy.deepcopy(page.recipe)
incoming.meta.name = 'From a Link'
incoming.meta.author = 'Ana'
alert = feedchefpages.present_import(window, account, incoming)
check('import: a recipe with the same id asks "Replace Recipe?"',
      alert.get_heading() == 'Replace Recipe?'
      and alert.get_body() == 'This replaces your recipe “Cooked Again”.'
      '\nChef: Ana'
      and alert.get_response_label('replace') == '_Replace'
      and alert.get_response_appearance('replace')
      == Adw.ResponseAppearance.DESTRUCTIVE
      and alert.get_response_label('cancel') == '_Cancel'
      and alert.get_default_response() == 'cancel'
      and alert.get_close_response() == 'cancel')
alert.emit('response', 'replace')
pump()
check('import: replacing stores the recipe and says so',
      store.get_recipe_by_id(incoming.id).meta.name == 'From a Link'
      and TOASTS[-1].text == 'Recipe imported')
check('import: the open recipe list reloads',
      any(r.get_title() == 'From a Link' for r in list_page._rows))
check('import: a built timeline of it cooks the new recipe, its title '
      'follows, and the pinned sidebar entry is rebuilt',
      page.recipe.meta.name == 'From a Link'
      and navpage.get_title() == 'From a Link' and refreshed)
settings.set_strv(feedchefpages.PINNED_KEY, [])
newcomer = default_recipe('Shiny')
newcomer.meta.author = 'Ana'
alert = feedchefpages.present_import(window, account, newcomer)
check('import: a new recipe asks "Import Recipe?" with name and author',
      alert.get_heading() == 'Import Recipe?'
      and alert.get_body()
      == 'Import “Shiny” into this account?\nChef: Ana'
      and alert.get_response_label('import') == '_Import'
      and alert.get_response_appearance('import')
      == Adw.ResponseAppearance.SUGGESTED
      and alert.get_default_response() == 'cancel')
alert.emit('response', 'cancel')
check('import: Cancel stores nothing',
      store.get_recipe_by_id(newcomer.id) is None)
alert = feedchefpages.present_import(window, account, newcomer)
alert.emit('response', 'import')
check('import: Import stores it',
      store.get_recipe_by_id(newcomer.id) is not None)
window.navigation = None
window.sidebar_host = None
feedchefpages.FeedChefBuilderDialog = Builder

# --- the page is freed, and its engine is shut down ---
window.set_child(None)
page_ref = page.weak_ref()
page = button = menu = navpage = navview = None
check('freed: a dropped Feed Chef page is freed', freed(page_ref))
check('freed: its engine was shut down', engine.shutdown_called)
window.destroy()
pump()

# --- a superseded cook is answered, never left open ---
from src.feedchefsource import FeedChefSource  # noqa: E402

_engine = FakeEngine([([], False, None), ([], False, None)])
_source = FeedChefSource(_engine, default_recipe())
_answers = []


def _answer(label):
    def callback(_source_object, result, *_data):
        try:
            _source.finish(result)
            _answers.append((label, None))
        except GLib.Error as error:
            _answers.append((label, error.code))
    return callback


_source.fetch({}, _answer('first'))
_source.fetch({}, _answer('second'))
pump_until(lambda: len(_answers) >= 2)
check('superseded cook: the first request is answered CANCELLED',
      ('first', int(Gio.IOErrorEnum.CANCELLED)) in _answers)
check('superseded cook: the second request is answered with its page',
      ('second', None) in _answers)
check('superseded cook: each request answered exactly once',
      len(_answers) == 2)

sys.exit(1 if failures else 0)
