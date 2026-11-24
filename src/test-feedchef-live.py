#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The recipe builder's newer controls over a fake counting worker: the heat
steps (Cool, Warm, Hot, Fire, and Custom for a percent that is none), the
sentence at the top and the live count under it (stale, then fresh; one
request for a burst of edits; fail-soft), and the Share Recipe dialog with
its QR code. Exits 77 (skip) without a display."""

import gc
import os
import sys
import tempfile

# Never the user's real dconf, data or state.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
_TMP = tempfile.mkdtemp(prefix='babble-feedchef-live-')
for _name in ('DATA', 'STATE', 'CACHE'):
    os.environ['XDG_%s_HOME' % _name] = os.path.join(_TMP, _name.lower())

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import (feedback, feedchefbuilder, feedchefbuilderlive,  # noqa: E402
                 feedchefshare)
from src.feedchef import deps, qrcode  # noqa: E402
from src.feedchef import storage as storagemod  # noqa: E402
from src.feedchef.livecook import LiveCount  # noqa: E402
from src.feedchef.recipe import Modifier, default_recipe  # noqa: E402
from src.feedchef.sharecodec import parse_feedchef_uri  # noqa: E402
from src.feedchefmodifiers import HeatStepsRow, heat_label  # noqa: E402

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


class Client:
    token = None
    instance_info = None
    instance = 'https://example.org'


class FakeLive:
    """The counting worker, answered by the test on the main loop."""

    made = []

    def __init__(self):
        self.requests = []
        self.closed = False
        self.superseded = 0
        self.matching = 7
        self.fail = False
        self.hold = False
        FakeLive.made.append(self)

    def request(self, recipe, on_done, on_error):
        self.requests.append(recipe)
        if self.hold:
            return
        matching, fail = self.matching, self.fail

        def deliver():
            if fail:
                on_error(RuntimeError('boom'))
            else:
                on_done(LiveCount(matching, 40))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)

    def supersede(self):
        self.superseded += 1

    def close(self):
        self.closed = True


Adw.init()
storagemod.set_root(os.path.join(_TMP, 'recipes'))
feedback.show_toast = lambda *_a, **_k: None
feedchefbuilderlive.make_live_cook = lambda _client, _account: FakeLive()
LISTS = []
feedchefbuilder.pages.call_api = (
    lambda _widget, _client, _start, on_done, _heading, finish=None:
    LISTS.append(on_done))

account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
store = feedchefbuilder.storage_for(account)
client = Client()
# An Adw.Window: a dialog only emits "closed" over one.
window = Adw.Window(default_width=900, default_height=700)
window.present()


def open_builder(recipe, is_new=True):
    dialog = feedchefbuilder.FeedChefBuilderDialog(
        client, account, recipe, is_new=is_new)
    dialog.present(window)
    pump()
    return dialog


def header_button(dialog, label):
    return find(dialog.header, Gtk.Button,
                lambda b: b.get_label() == label)[0]


def editor(dialog, kind):
    return [e for e in dialog.editors if e.kind == kind][0]


# --- the heat steps

dialog = open_builder(default_recipe('Steps'))
heat_editor = editor(dialog, 'heat')
row = heat_editor.spin
group = row.group
names = [group.get_toggle(i).get_name() for i in range(group.get_n_toggles())]
labels = [group.get_toggle(i).get_label() for i in range(group.get_n_toggles())]
check('heat: the percent slider is a toggle group of three levels, coolest '
      'first, with no Cool (the switch is off) (%s)' % labels,
      isinstance(row, HeatStepsRow) and isinstance(group, Adw.ToggleGroup)
      and names == ['warm', 'hot', 'fire']
      and labels == ['Warm', 'Hot', 'Fire'])
check('heat: a new heat modifier is Warm, which is 50 percent',
      group.get_active_name() == 'warm' and row.get_value() == 50.0)
hint = ' '.join(w.get_label() for w in find(row, Gtk.Label))
check('heat: the row says what each level hides and that a page keeps 5',
      'Warm hides half of the posts, Hot three in four, Fire nine in ten'
      in hint and 'never keeps fewer than 5 posts' in hint)
check('heat: the expander subtitle is short',
      heat_editor.row.get_subtitle() == 'Hides the least engaging posts')
heat_editor.row.set_enable_expansion(True)
check('heat: the subtitle names the level',
      heat_editor.row.get_subtitle().endswith(' · Warm'))
for name, percent in (('fire', 90), ('hot', 75), ('warm', 50)):
    group.set_active_name(name)
    check('heat: picking %s makes it %d percent, in the subtitle too'
          % (name, percent), row.get_value() == percent
          and heat_editor.row.get_subtitle().endswith(
              ' · %s' % name.capitalize()))
group.set_active_name('fire')
check('heat: a level is an edit', not dialog.get_can_close())
header_button(dialog, '_Save').emit('clicked')
pump()
saved = store.get_recipe_by_id(dialog.recipe.id)
check('heat: Save stores the percent of the level (90)',
      [m.percentile_threshold for m in saved.kitchen.modifiers
       if m.type == 'heat'] == [90])

old = default_recipe('Old')
old.kitchen.modifiers = [Modifier(type='heat', percentile_threshold=50)]
store.save_recipe(old)
dialog = open_builder(store.get_recipe_by_id(old.id), False)
heat_editor = editor(dialog, 'heat')
group = heat_editor.spin.group
check('heat: an old recipe with 50 loads as Warm, unchanged and no edit',
      group.get_active_name() == 'warm' and dialog.get_can_close()
      and group.get_n_toggles() == 3)
dialog.force_close()
pump()

odd = default_recipe('Odd')
odd.kitchen.modifiers = [Modifier(type='heat', percentile_threshold=62.5)]
store.save_recipe(odd)
dialog = open_builder(store.get_recipe_by_id(odd.id), False)
heat_editor = editor(dialog, 'heat')
row = heat_editor.spin
group = row.group
check('heat: another percent selects no toggle and is said under the title '
      '(%s)' % group.get_active_name(),
      group.get_n_toggles() == 3 and group.get_active_name() is None
      and row.imported.get_visible() and row.imported.get_label()
      == 'Imported value: 62.5%. Choose a level to replace it.'
      and row.get_value() == 62.5 and dialog.get_can_close()
      and heat_editor.row.get_subtitle().endswith(
          ' · Imported level (62.5%)'))
dialog.name_row.set_text('Odd Renamed')
header_button(dialog, '_Save').emit('clicked')
pump()
check('heat: a save that did not touch it keeps the exact percent (62.5)',
      [m.percentile_threshold for m in store.get_recipe_by_id(
          odd.id).kitchen.modifiers if m.type == 'heat'] == [62.5])
dialog = open_builder(store.get_recipe_by_id(odd.id), False)
heat_editor = editor(dialog, 'heat')
group = heat_editor.spin.group
group.set_active_name('hot')
check('heat: picking a level replaces it (75) and hides the note',
      heat_editor.spin.get_value() == 75.0
      and not heat_editor.spin.imported.get_visible())
header_button(dialog, '_Save').emit('clicked')
pump()
check('heat: and the level is what is stored',
      [m.percentile_threshold for m in store.get_recipe_by_id(
          odd.id).kitchen.modifiers if m.type == 'heat'] == [75])
none_set = default_recipe('NoPercent')
none_set.kitchen.modifiers = [Modifier(type='heat')]
store.save_recipe(none_set)
dialog = open_builder(store.get_recipe_by_id(none_set.id), False)
heat_editor = editor(dialog, 'heat')
check('heat: a modifier with no percent shows what the engine does (0, '
      'nothing hidden), not Warm',
      heat_editor.spin.get_value() == 0.0
      and heat_editor.spin.group.get_active_name() is None)
check('heat: the label of a percent is its level, "every post" for none, '
      'or the imported one',
      [heat_label(p) for p in (0, 50, 75, 90, 62.5, 150)]
      == ['every post', 'Warm', 'Hot', 'Fire', 'Imported level (62.5%)',
          'Imported level (100%)'])
check('trending: its minimum is no longer called heat',
      any(r.get_title() == 'Minimum Momentum'
          for r in find(dialog.page, Adw.SpinRow)))
dialog.force_close()
pump()

# --- the sentence and the live count

FakeLive.made.clear()
recipe = default_recipe('Count')
dialog = open_builder(recipe)
live = FakeLive.made[-1]
check('summary: the builder asks for the first count as it opens, once',
      len(live.requests) == 1)
check('summary: the sentence is at the top, above the recipe group',
      dialog.summary.sentence_label.get_label() == 'Posts from Home, up to 3 posts '
      'per person, most engaging first'
      and dialog.page.get_first_child() is not None)
check('summary: the count reads "7 posts match" and where it comes from',
      pump_until(lambda: dialog.summary.count_row.get_title() == '7 posts match')
      and dialog.summary.count_row.get_subtitle() == 'Out of the latest 40 posts'
      and not dialog.summary.count_row.has_css_class('dimmed'))
dialog.sensitive.set_active(True)
check('summary: an edit supersedes the request in flight, so a stale answer '
      'never shows as fresh', live.superseded >= 1)
check('summary: the sentence follows an edit at once, with no waiting',
      dialog.summary.sentence_label.get_label() == 'Posts from Home, without '
      'sensitive posts and up to 3 posts per person, most engaging first')
check('summary: the count goes stale, not blank: dimmed, "Updating…", the '
      'old number kept', dialog.summary.count_row.has_css_class('dimmed')
      and dialog.summary.count_row.get_subtitle() == 'Updating…'
      and dialog.summary.count_row.get_title() == '7 posts match'
      and dialog.summary.count_spinner.get_spinning())
check('summary: no request is made inside the edit itself',
      len(live.requests) == 1)
live.matching = 1
dialog.positive.set_active(True)
dialog.media.set_active(True)
dialog.alt.set_active(True)
check('summary: a burst of edits makes one request, a debounce later '
      '(%d)' % len(live.requests), len(live.requests) == 1
      and pump_until(lambda: len(live.requests) == 2, 1.0))
check('summary: it asks for what is on screen, a copy of the recipe',
      live.requests[-1].diet.hard_filters.must_have_media
      and live.requests[-1].diet.hard_filters.positive_only
      and live.requests[-1] is not dialog.recipe)
check('summary: and the answer lands within half a second of the debounce '
      '(singular for one)',
      pump_until(lambda: dialog.summary.count_row.get_title() == '1 post matches',
                 0.5) and not dialog.summary.count_row.has_css_class('dimmed')
      and not dialog.summary.count_spinner.get_spinning())
check('summary: the count is mirrored into the Preview Recipe row (%s)'
      % dialog.preview_row.get_subtitle(),
      dialog.preview_row.get_subtitle() == '1 of 40 posts matches')
live.fail = True
dialog.min_length.set_value(30)
pump_until(lambda: len(live.requests) == 3, 1.0)
check('summary: a count that fails says so and offers to try again, and '
      'the form is untouched',
      pump_until(lambda: dialog.summary.count_row.get_title()
                 == 'Could not count posts')
      and dialog.summary.count_retry.get_visible()
      and dialog.save_button.get_sensitive())
live.fail = False
dialog.summary.count_retry.emit('clicked')
check('summary: Count Again asks again right away',
      pump_until(lambda: dialog.summary.count_row.get_title() == '7 posts match'
                 or dialog.summary.count_row.get_title() == '1 post matches')
      and len(live.requests) == 4)
dialog.scope.set_selected(3)
check('summary: a list scope with no list yet cannot be counted, and says '
      'so', dialog.summary.count_row.get_title() == 'Choose a list to count posts'
      and not dialog.summary.count_spinner.get_spinning()
      and live.superseded >= 1)
dialog.force_close()
check('summary: closing the builder closes the worker',
      pump_until(lambda: live.closed))

# --- the share dialog

if deps.unavailable_reason('segno'):
    print('skip - segno is not installed: the QR checks are skipped')
else:
    recipe = default_recipe('Share Me')
    shared = feedchefshare.FeedChefShareDialog(recipe, account)
    shared.present(window)
    check('share: the link is the recipe\'s, with the sharer as its chef',
          parse_feedchef_uri(shared.link).meta.author == '@me@example.org'
          and shared.link_row.get_subtitle().startswith('babble://feedchef'))
    check('share: the QR code is drawn after the dialog opens (a worker '
          'makes it)', pump_until(lambda: shared.qr_picture is not None))
    side, pixels = shared.qr_pixels
    check('share: it is the code of the link, black on white, with a quiet '
          'zone (%d x %d)' % (side, side),
          qrcode.render(shared.link) == (side, pixels)
          and set(pixels) == {0, 255})
    texture = shared.qr_picture.get_paintable().texture
    check('share: one pixel a module in a grey texture, scaled by whole '
          'pixels', texture.get_width() == side
          and texture.get_height() == side
          and shared.qr_picture.get_size_request()[0] % side == 0)
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_DARK)
    pump()
    check('share: the pixels are the same in the dark style',
          shared.qr_pixels == (side, pixels))
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.DEFAULT)
    shared.copy_row.emit('activated')
    got = []
    shared.get_display().get_clipboard().read_text_async(
        None, lambda c, r: got.append(c.read_text_finish(r)))
    check('share: Copy Link puts the link on the clipboard',
          pump_until(lambda: got) and got[0] == shared.link)
    shared.force_close()
    pump()

    real = deps.unavailable_reason
    deps.unavailable_reason = lambda *names: (
        'segno is not installed' if names == ('segno',) else real(*names))
    bare = feedchefshare.FeedChefShareDialog(recipe, account)
    bare.present(window)
    pump()
    check('share: without segno the dialog is the link and Copy Link only',
          not bare.qr_group.get_visible() and bare.qr_picture is None
          and bare.copy_row.get_visible())
    bare.force_close()
    deps.unavailable_reason = real

    long_recipe = default_recipe('Long')
    long_recipe.diet.keyword_blocklist = ['word%d' % n for n in range(400)]
    big = feedchefshare.FeedChefShareDialog(long_recipe, account)
    big.present(window)
    check('share: a recipe too big for any code shows the link alone',
          pump_until(lambda: not big.qr_group.get_visible(), 5.0)
          and big.qr_picture is None)
    big.force_close()

    dialog = open_builder(default_recipe('From Builder'))
    shares = [r for r in find(dialog.page, Adw.ButtonRow)
              if r.get_title() == 'S_hare Recipe']
    shares[0].emit('activated')
    pump()
    check('share: the builder\'s Share Recipe row is there once',
          len(shares) == 1)
    dialog.force_close()
    pump()

if not deps.unavailable_reason('segno'):
    evil = default_recipe('Evil')
    evil.meta.author = '<a href="x">@a@b</a> & <b>bold</b>'
    nasty = feedchefshare.FeedChefShareDialog(evil, account)
    nasty.present(window)
    pump()
    group = [g for g in find(nasty.page, Adw.PreferencesGroup)][-1]
    check('share: the chef of a recipe is shown as text, never as markup',
          group.get_description().count('&lt;a href') == 1
          and '<a href' not in group.get_description()
          and 'Scan the code or copy the link to share this recipe.'
          in group.get_description())
    nasty.force_close()
    pump()
    plain = default_recipe('Plain')
    scan = feedchefshare.FeedChefShareDialog(plain, account)
    scan.present(window)
    pump_until(lambda: scan.qr_picture is not None)
    side = scan.qr_pixels[0]
    size = scan.qr_picture.get_size_request()[0]
    check('share: the code is drawn with at least 3 pixels a module '
          '(%d modules, %d px)' % (side, size),
          size // side >= feedchefshare.MIN_MODULE and size % side == 0)
    scan.force_close()
    pump()
    huge = default_recipe('Huge')
    import random  # noqa: E402
    rng = random.Random(5)
    huge.diet.keyword_blocklist = [
        ''.join(rng.choice('abcdefghijklmnopqrstuvwxyz')
                for _n in range(9)) for _m in range(400)]
    big = feedchefshare.FeedChefShareDialog(huge, account)
    big.present(window)
    check('share: a link too long for a code of a size that scans says so '
          'and keeps Copy Link', pump_until(lambda: big.too_large_row
                                            .get_visible())
          and big.too_large_row.get_title() == 'This recipe is too large '
          'for a QR code. Copy the link instead.'
          and not big.qr_group.get_visible() and big.copy_row.get_visible())
    big.force_close()
    pump()

window.destroy()
pump()
gc.collect()
sys.exit(1 if failures else 0)
