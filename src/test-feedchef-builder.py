#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's builder over a fake engine: the positive-only switch, the
sentiment and trending editors, the icon picker (and where the icon shows:
the recipe list, the pinned sidebar entry), and the preview dialog. Exits 77
(skip) without a display."""

import os
import tempfile

# Never the user's real dconf, data or state.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
_TMP = tempfile.mkdtemp(prefix='babble-feedchef-builder-')
for _name in ('DATA', 'STATE', 'CACHE'):
    os.environ['XDG_%s_HOME' % _name] = os.path.join(_TMP, _name.lower())

import gc  # noqa: E402
import glob  # noqa: E402
import sys  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')

from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import (feedback, feedchefbuilder, feedchefpages,  # noqa: E402
                 feedchefpreview, navigation)
from src.feedchef import icons  # noqa: E402
from src.feedchef import storage as storagemod  # noqa: E402
from src.feedchef.engine import FeedResult  # noqa: E402
from src.feedchef.recipe import Modifier, default_recipe  # noqa: E402
from src.statusmenu import StatusMenuButton  # noqa: E402
from src.statusrun import StatusRunRow  # noqa: E402
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
                     on_done=None, on_error=None):
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
# The real app's setup: the resource bundle registered and an application
# with the app id, which is what puts the bundled icons in the theme.
BUILDDIR = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
BUNDLES = sorted(glob.glob(os.path.join(BUILDDIR, '**', 'babble.gresource'),
                           recursive=True))
check('setup: the resource bundle is built', bool(BUNDLES))
if BUNDLES:
    Gio.resources_register(Gio.Resource.load(BUNDLES[0]))
app = Adw.Application(application_id='io.github.mauriciobc.Babble',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register(None)
storagemod.set_root(os.path.join(_TMP, 'recipes'))

# Toasts are recorded, not shown: an Undo toast is answered by the test.
TOASTS = []


def fake_toast(_widget, text, undo=None, undo_label=None, dismissed=None):
    TOASTS.append(SimpleNamespace(text=text, undo=undo,
                                  dismissed=dismissed))
    return TOASTS[-1]


feedback.show_toast = fake_toast
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

list_page = feedchefpages.FeedChefListPage(client, account)
window.set_child(list_page)
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



PREVIEW_BATCHES = []
mood = default_recipe('Mood')
mood.kitchen.modifiers = [Modifier(type='trending', growth_threshold=0.4)]
mood.meta.icon = 'restaurant'
dialog = open_builder(mood, True)
positive_rows = find(dialog.page, Adw.SwitchRow,
                     lambda r: r.get_title() == 'Only Positive Posts')
check('builder: Only Positive Posts is a switch with a plain subtitle',
      len(positive_rows) == 1 and positive_rows[0] is dialog.positive
      and positive_rows[0].get_subtitle()
      == 'Hides posts that read as negative. English and Portuguese only.'
      and not dialog.positive.get_active())
sentiment_editor = [e for e in dialog.editors
                    if e.kind == 'sentiment_boost'][0]
trending_editor = [e for e in dialog.editors if e.kind == 'trending'][0]
check('builder: Favor Positive Posts has the same language note',
      sentiment_editor.row.get_subtitle() == 'Posts that read as '
      'positive rank higher. English and Portuguese only.')
adjustment = sentiment_editor.spin.get_adjustment()
check('builder: Favor Positive Posts is a modifier editor, off, 10 to 100 '
      'percent in steps of 10, 50 by default',
      sentiment_editor.row.get_title() == 'Favor Positive Posts'
      and not sentiment_editor.row.get_enable_expansion()
      and sentiment_editor.spin.get_title() == 'Strength'
      and (adjustment.get_lower(), adjustment.get_upper(),
           adjustment.get_step_increment()) == (10, 100, 10)
      and sentiment_editor.spin.get_value() == 50)
adjustment = trending_editor.spin.get_adjustment()
check('builder: trending growth is a percent, 10 to 100, shown as read',
      (adjustment.get_lower(), adjustment.get_upper(),
       adjustment.get_step_increment()) == (10, 100, 10)
      and trending_editor.spin.get_value() == 40)
shown = ' '.join(
    '%s %s' % (r.get_title(), r.get_subtitle())
    for r in find(dialog.page, Adw.PreferencesRow)
    if hasattr(r, 'get_subtitle'))
check('builder: no engine or algorithm names are shown',
      not any(word in shown for word in
              ('VADER', 'LeIA', 'TF-IDF', 'degree', 'similarity cluster')))
check('builder: the unknown icon shows the chef hat, and is no edit',
      dialog.icon_image.get_icon_name() == 'babble-feed-chef-symbolic'
      and dialog.get_can_close())
check('builder: the unsupported row no longer names sentiment',
      not find(dialog.page, Adw.ActionRow, lambda r: r.get_title()
               == 'Some Ingredients Are Not Supported Yet'))
dialog.positive.set_active(True)
check('builder: the positive-only switch is an edit', not dialog.get_can_close())
sentiment_editor.row.set_enable_expansion(True)
sentiment_editor.spin.set_value(70)
trending_editor.spin.set_value(60)
header_button(dialog, '_Save').emit('clicked')
pump()
after = store.get_recipe_by_id(mood.id)
mods = {m.type: m for m in after.kitchen.modifiers}
check('builder: positive_only is written', after.diet.hard_filters.positive_only)
check('builder: the sentiment strength maps to 0.1-1.0',
      mods['sentiment_boost'].sentiment_boost == 0.7)
check('builder: the trending percent maps to growth_threshold',
      mods['trending'].growth_threshold == 0.6)
dialog = open_builder(after, False)
check('builder: a stored sentiment modifier is read back as percent',
      [e for e in dialog.editors if e.kind == 'sentiment_boost'][0]
      .spin.get_value() == 70 and dialog.positive.get_active())
dialog.positive.set_active(False)
[e for e in dialog.editors
 if e.kind == 'sentiment_boost'][0].row.set_enable_expansion(False)
header_button(dialog, '_Save').emit('clicked')
pump()
after = store.get_recipe_by_id(mood.id)
check('builder: switching sentiment off removes the modifier, positive '
      'only follows its switch',
      'sentiment_boost' not in [m.type for m in after.kitchen.modifiers]
      and not after.diet.hard_filters.positive_only)
plain = default_recipe('Plain Sentiment')
dialog = open_builder(plain, True)
[e for e in dialog.editors
 if e.kind == 'sentiment_boost'][0].row.set_enable_expansion(True)
header_button(dialog, '_Save').emit('clicked')
pump()
check('builder: a new sentiment modifier defaults to 0.5',
      [m for m in store.get_recipe_by_id(plain.id).kitchen.modifiers
       if m.type == 'sentiment_boost'][0].sentiment_boost == 0.5)

# The modifiers' subtitles show their value while they are on.
dialog = open_builder(default_recipe('Subtitles'), True)
sentiment_editor = [e for e in dialog.editors
                    if e.kind == 'sentiment_boost'][0]
trending_editor = [e for e in dialog.editors if e.kind == 'trending'][0]
fresh = [e for e in dialog.editors if e.kind == 'freshness'][0]
heat = [e for e in dialog.editors if e.kind == 'heat'][0]
diversity = [e for e in dialog.editors if e.kind == 'diversity'][0]
fresh.row.set_enable_expansion(False)
diversity.row.set_enable_expansion(False)
check('modifiers: a modifier that is off shows only what it does',
      fresh.row.get_subtitle() == 'Newer posts rank higher'
      and diversity.row.get_subtitle()
      == 'Keeps one voice from filling the feed')
fresh.row.set_enable_expansion(True)
check('modifiers: turning one on adds its value to the subtitle',
      fresh.row.get_subtitle() == 'Newer posts rank higher · 6 hours')
fresh.spin.set_value(1)
check('modifiers: the value follows the spin row, singular for one',
      fresh.row.get_subtitle() == 'Newer posts rank higher · 1 hour')
heat.row.set_enable_expansion(True)
diversity.row.set_enable_expansion(True)
trending_editor.row.set_enable_expansion(True)
sentiment_editor.row.set_enable_expansion(True)
trending_editor.spin.set_value(30)
check('modifiers: percent and per-person values are shown',
      heat.row.get_subtitle().endswith(' · 50%')
      and diversity.row.get_subtitle().endswith(' · 3 per person')
      and trending_editor.row.get_subtitle().endswith(' · 30%')
      and sentiment_editor.row.get_subtitle().endswith(' · 50%'))
trending_editor.row.set_enable_expansion(False)
check('modifiers: turning one off takes the value away again',
      trending_editor.row.get_subtitle()
      == 'Posts about what is rising on the server rank higher')
dialog.force_close()
pump()
dialog = open_builder(default_recipe('Actions'), True)
action_rows = {r.get_title(): r for r in find(dialog.page, Adw.ButtonRow)}
check('builder: Preview Recipe and Copy Share Link have start icons',
      action_rows['_Preview Recipe'].get_start_icon_name()
      == 'view-reveal-symbolic'
      and action_rows['Copy Share _Link'].get_start_icon_name()
      == 'edit-copy-symbolic')
dialog.force_close()
pump()

# A growth threshold imported outside the editor's range is not rewritten
# unless the reader changes it.
wild = default_recipe('Wild')
wild.kitchen.modifiers = [Modifier(type='trending', growth_threshold=2.5)]
dialog = open_builder(wild, True)
dialog.name_row.set_text('Wild Renamed')
header_button(dialog, '_Save').emit('clicked')
pump()
check('builder: an imported out-of-range growth_threshold is kept on save',
      store.get_recipe_by_id(wild.id).kitchen.modifiers[0]
      .growth_threshold == 2.5)
dialog = open_builder(store.get_recipe_by_id(wild.id), False)
[e for e in dialog.editors if e.kind == 'trending'][0].spin.set_value(50)
header_button(dialog, '_Save').emit('clicked')
pump()
check('builder: a changed growth_threshold is written',
      store.get_recipe_by_id(wild.id).kitchen.modifiers[0]
      .growth_threshold == 0.5)

# The icon picker.
JAVA_ICONS = (
    'HEART STAR PEOPLE CITY IMAGE NEWS FEED COLOR_PALETTE CAT DOG RABBIT '
    'TURTLE ACADEMIC_CAP BOT IMPORTANT PIN SHIELD CHAT TAG TRAIN BICYCLE '
    'MAP BACKPACK BRIEFCASE BOOK LANGUAGE WEATHER APERTURE MUSIC LOCATION '
    'GLOBE MEGAPHONE MICROPHONE MICROSCOPE STETHOSCOPE KEYBOARD COFFEE '
    'CLAPPER_BOARD LAUGH BALLOON PI MATH_FORMULA GAMES CODE BUG LIGHT_BULB '
    'FIRE LEAVES SPORT HEALTH PIZZA GAVEL GAUGE HEADPHONES HUMAN BEAKER BED '
    'RECYCLE_BIN VERIFIED DOCTOR DIAMOND UMBRELLA WATER SUN SUNSET CLOUD '
    'THUNDERSTORM RAIN SNOWFLAKE GNOME').split()
table = icons.ICONS
theme = Gtk.IconTheme.get_for_display(window.get_display())
check('icons: every key is a name of mashdon\'s Icon enum',
      all(e in JAVA_ICONS for e, _icon in table)
      and len({e for e, _icon in table}) == len(table))
check('icons: 16 to 20 entries, PIZZA the default among them',
      16 <= len(table) <= 20 and 'PIZZA' in dict(table)
      and default_recipe('x').meta.icon == 'PIZZA')
check('icons: no two entries share an icon name',
      len({icon for _e, icon in table}) == len(table))
missing = [icon for _e, icon in table if not theme.has_icon(icon)]
check('icons: the theme has every icon, under the app\'s own setup',
      not missing and theme.has_icon(icons.DEFAULT_ICON))
if missing:
    print('  missing: %s' % missing)
check('icons: unknown, empty and legacy names show the chef hat',
      icons.icon_name('NOPE') == icons.DEFAULT_ICON
      and icons.icon_name(None) == icons.DEFAULT_ICON
      and icons.icon_name('restaurant') == icons.DEFAULT_ICON
      and icons.icon_name('CHEF') == icons.DEFAULT_ICON
      and icons.icon_name('MUSIC') == 'audio-x-generic-symbolic'
      and icons.icon_name('pizza') == icons.icon_name('PIZZA'))
check('icons: every entry has a label', all(
    feedchefbuilder.icon_label(e) for e, _icon in table))
icon_recipe = default_recipe('Iconic')
dialog = open_builder(icon_recipe, True)
buttons = find(dialog.icon_grid, Gtk.ToggleButton)


check('builder: the icon popover has a flat button per entry with a '
      'tooltip naming it',
      len(buttons) == len(table)
      and all(b.get_tooltip_text() and b.has_css_class('flat')
              for b in buttons)
      and dialog.icon_menu.get_tooltip_text() == 'Choose Icon'
      and dialog.icon_image.get_icon_name() == 'emoji-food-symbolic')
checked = [b.get_tooltip_text() for b in buttons if b.get_active()]
check('builder: the popover marks the current icon, and only it',
      checked == ['Food'])
check('builder: the Icon row says the choice, and its image is decorative',
      dialog.icon_row.get_subtitle() == 'Food'
      and dialog.icon_image.get_accessible_role()
      == Gtk.AccessibleRole.PRESENTATION)
dialog.activate_action('icon.choose', GLib.Variant.new_string('MUSIC'))
check('builder: choosing an icon updates the prefix and is an edit',
      dialog.icon_image.get_icon_name() == 'audio-x-generic-symbolic'
      and not dialog.get_can_close())
check('builder: choosing an icon moves the mark and the subtitle',
      [b.get_tooltip_text() for b in buttons if b.get_active()]
      == ['Music'] and dialog.icon_row.get_subtitle() == 'Music')
dialog.activate_action('icon.choose', GLib.Variant.new_string('PIZZA'))
check('builder: choosing the first icon again makes it clean again',
      dialog.get_can_close())
dialog.activate_action('icon.choose', GLib.Variant.new_string('MUSIC'))
header_button(dialog, '_Save').emit('clicked')
pump()
check('builder: the icon is stored as its enum name',
      store.get_recipe_by_id(icon_recipe.id).meta.icon == 'MUSIC')
list_page.reload()
row = list_page._row_by_id[icon_recipe.id]
check('list: a recipe row has its icon as prefix',
      'audio-x-generic-symbolic'
      in [i.get_icon_name() for i in find(row, Gtk.Image)])
unknown = default_recipe('Mystery')
unknown.meta.icon = 'NOT_AN_ICON'
store.save_recipe(unknown)
list_page.reload()
check('list: an unknown icon shows the chef hat',
      'babble-feed-chef-symbolic' in [
          i.get_icon_name() for i in
          find(list_page._row_by_id[unknown.id], Gtk.Image)])
routing = navigation.Navigation(SimpleNamespace(account=lambda: account))
pins = SimpleNamespace(get_strv=lambda key: (
    ['1/%s' % icon_recipe.id, '1/%s' % unknown.id, '1/gone']
    if key == 'favorite-feedchef-ids' else []))
check('sidebar: a pinned recipe uses its icon, an unknown one the chef '
      'hat, a missing recipe the Feed Chef place',
      routing.pinned_routes(pins, '1') == [
          ('recipe:%s' % icon_recipe.id, 'Iconic',
           'audio-x-generic-symbolic'),
          ('recipe:%s' % unknown.id, 'Mystery',
           'babble-feed-chef-symbolic'),
          ('recipe:gone', 'Feed Chef', 'babble-feed-chef-symbolic')])
check('sidebar: the Feed Chef place keeps the chef hat',
      ('feed-chef', 'babble-feed-chef-symbolic', 0) in navigation.PLACES)
for stored in store.load_recipes():
    store.delete_recipe(stored.id)

# --- the preview ---
PREVIEW_BATCHES.append(([status(11), status(12)], False, None))
cooked = []
preview_engines = []
feedchefpreview.make_engine = (
    lambda _c, _k, _i, recipe_: cooked.append(recipe_)
    or preview_engines.append(FakeEngine(PREVIEW_BATCHES))
    or preview_engines[-1])
previews = []
Preview = feedchefbuilder.FeedChefPreviewDialog
feedchefbuilder.FeedChefPreviewDialog = (
    lambda *args: previews.append(Preview(*args)) or previews[-1])
unsaved = default_recipe('Saved Name')
store.save_recipe(unsaved)
# An AdwWindow hosts the dialogs, as the app's window does: Adw.Dialog
# only emits "closed" there (a plain GtkWindow gets a fallback).
host = Adw.Window(default_width=900, default_height=700)
host.present()
dialog = open_builder(unsaved, False, host)
dialog.name_row.set_text('Unsaved Name')
dialog.positive.set_active(True)
preview_rows = find(dialog.page, Adw.ButtonRow,
                    lambda r: r.get_title() == '_Preview Recipe')
check('preview: Preview Recipe sits with Copy Share Link in the bottom '
      'group',
      len(preview_rows) == 1 and preview_rows[0].get_parent().get_parent()
      is find(dialog.page, Adw.ButtonRow,
              lambda r: r.get_title() == 'Copy Share _Link')[0]
      .get_parent().get_parent())
preview_rows[0].emit('activated')
pump()
preview = previews[0]
check('preview: it is a dialog titled Preview over a TimelinePage',
      preview.get_title() == 'Preview'
      and isinstance(preview.timeline, TimelinePage))
check('preview: it shows the fake engine\'s posts',
      pump_until(lambda: ids(preview.timeline) == ['00011', '00012']))
check('preview: it cooks the unsaved edits, on a copy',
      cooked[0].meta.name == 'Unsaved Name'
      and cooked[0].diet.hard_filters.positive_only
      and cooked[0] is not dialog.recipe
      and store.get_recipe_by_id(unsaved.id).meta.name == 'Saved Name')
check('preview: no focus banner and no More Like This hook',
      not hasattr(preview.timeline, 'banner')
      and getattr(preview.timeline, 'post_menu_entries', None) is None)
probe = StatusMenuButton()
probe.bind(StatusItem(status(11)), client)
preview.timeline.stack.add_named(probe, 'probe')
probe.prepare()
menu = probe.get_menu_model()
labels = []
for section in range(menu.get_n_items()):
    inner = menu.get_item_link(section, 'section')
    for index in range(inner.get_n_items() if inner else 0):
        labels.append(inner.get_item_attribute_value(
            index, 'label', None).get_string())
check('preview: the post menu has no More Like This',
      'More _Like This' not in labels)
check('preview: the header has no actions of its own',
      not find(preview.header, Gtk.Button, lambda b: b.get_label()))
check('preview: the title is Preview, the recipe name its subtitle',
      isinstance(preview.header.get_title_widget(), Adw.WindowTitle)
      and preview.header.get_title_widget().get_title() == 'Preview'
      and preview.header.get_title_widget().get_subtitle()
      == 'Unsaved Name')

# Read-only: nothing in a post can be used from the preview.
class Recorder:
    def __init__(self):
        self.pushed = []

    def push(self, page):
        self.pushed.append(page)

    def add(self, page):
        self.pushed.append(page)


recorder = Recorder()
host.navigation_view = recorder
pump(60)
view = preview.timeline.list_view
cards = find(view, StatusRunRow)
check('preview: the posts are on screen as cards', len(cards) >= 1)
controls = [w for card in cards for w in descendants(card)
            if isinstance(w, (Gtk.Button, Gtk.MenuButton, Adw.Avatar))]
check('preview: the cards have an action bar, a menu and an avatar',
      any(isinstance(w, StatusMenuButton) for w in controls)
      or any(isinstance(w, Gtk.MenuButton) for w in controls))


def reached_by_pointer(widget):
    """True when a click on *widget* would land inside its card."""
    point = widget.compute_point(
        view, Graphene.Point().init(widget.get_width() / 2,
                                    widget.get_height() / 2))
    if not point[0]:
        return False
    target = view.pick(point[1].x, point[1].y, Gtk.PickFlags.DEFAULT)
    while target is not None and target is not view:
        if isinstance(target, StatusRunRow):
            return True
        target = target.get_parent()
    return False


mapped = [w for w in controls if w.get_mapped() and w.get_width() > 0]
check('preview: a click on an action, the menu or an avatar reaches '
      'nothing', bool(mapped) and not any(reached_by_pointer(w)
                                          for w in mapped))
check('preview: none of them can take the keyboard focus',
      not any(w.grab_focus() for w in controls))
view.emit('activate', 0)
preview.timeline.on_row_activated(view, 0)
pump()
check('preview: activating a post pushes no page on the window',
      recorder.pushed == [])
scrolled = preview.timeline._scrolled
adjustment = scrolled.get_vadjustment()
check('preview: it still scrolls, by the wheel and by keyboard',
      scrolled.get_focusable() and scrolled.get_sensitive()
      and adjustment.get_upper() > 0)
host.navigation_view = None

preview_engine = preview_engines[0]
check('preview: its engine runs until the dialog closes',
      not preview_engine.shutdown_called)
preview.force_close()
check('preview: closing shuts its engine down',
      pump_until(lambda: preview_engine.shutdown_called))


def open_preview(recipe_, fail_first=False, batches=()):
    """A preview built directly, over an engine that fails once when
    asked to."""
    PREVIEW_BATCHES[:] = list(batches)
    preview_engines.clear()
    feedchefpreview.make_engine = (
        lambda _c, _k, _i, _r: preview_engines.append(
            FakeEngine(PREVIEW_BATCHES)) or preview_engines[-1])
    original = FakeEngine.process_feed

    def process_feed(self, *args, **kwargs):
        self.fail = self.fail or (fail_first and not self.calls)
        return original(self, *args, **kwargs)
    FakeEngine.process_feed = process_feed
    try:
        dialog_ = Preview(client, account, recipe_)
    finally:
        FakeEngine.process_feed = original
    dialog_.present(host)
    return dialog_


retry_recipe = default_recipe('Retry')
retry_recipe.meta.icon = 'MUSIC'
# The failing cook still takes its batch off the queue.
broken = open_preview(retry_recipe, fail_first=True,
                      batches=[([], False, None),
                               ([status(21)], False, None)])
check('preview: a failed cook shows the error page',
      pump_until(lambda: broken.timeline.stack.get_visible_child_name()
                 == 'error'))
retry = find(broken.timeline.error_page, Gtk.Button)[0]
retry.emit('clicked')
check('preview: Try Again cooks again and shows the posts',
      pump_until(lambda: ids(broken.timeline) == ['00021'])
      and broken.timeline.stack.get_visible_child_name() == 'list')
broken.force_close()
pump()
empty = open_preview(retry_recipe)
check('preview: with nothing to show the empty page has the recipe icon',
      pump_until(lambda: empty.timeline.stack.get_visible_child_name()
                 == 'empty')
      and empty.timeline.empty_page.get_icon_name()
      == 'audio-x-generic-symbolic')
empty.force_close()
pump()
feedchefbuilder.FeedChefPreviewDialog = Preview
dialog.force_close()
pump()
host.destroy()
store.delete_recipe(unsaved.id)

# The deprecated style class is not used by Feed Chef's UI.
for name in ('feedchefbuilder.py', 'feedchefpages.py',
             'feedchefpreview.py'):
    with open(os.path.join(srcdir, name), encoding='utf-8') as handle:
        check('style: %s does not use dim-label' % name,
              'dim-label' not in handle.read())

window.destroy()
pump()

sys.exit(1 if failures else 0)
