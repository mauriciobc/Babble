#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: F403, F405
"""The quality layer on Feed Chef's screens, part one: the Kitchen Report's
Quality section (rendered from a real audit record, the numbers matching the
core's), Apply with its Undo, the health badge of the recipe list and the
alert's banner and toast, all computed on the quality worker and never the
main thread. The engine is a fake (see ``feedchefaudit/uikit.py``). Exits 77
(skip) without a display."""

import os
import tempfile

# Never the user's real dconf, data or state.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
_TMP = tempfile.mkdtemp(prefix='babble-feedchef-quality-ui-')
for _name in ('DATA', 'STATE', 'CACHE'):
    os.environ['XDG_%s_HOME' % _name] = os.path.join(_TMP, _name.lower())

import sys  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(srcdir)),
                                'bench'))
sys.path.insert(0, srcdir)

from feedchefaudit.uikit import *  # noqa: E402

# --- 1. the Kitchen Report's Quality section ---------------------------------

reset()
recipe = default_recipe('Cook')
BASE_CALL = {'n': 0}
page = open_page(recipe, lambda r, v, m, p: copy.deepcopy(BASE))
page.report_button.emit('clicked')
dialog = None
pump_until(lambda: dialogs(feedchefexplain.KitchenReportDialog))
dialog = dialogs(feedchefexplain.KitchenReportDialog)[-1]
check('report: the Quality section is there, waiting, before the numbers',
      dialog.quality.quality_group.get_title() == 'Quality')
shown = pump_until(lambda: any(
    r.get_title() == 'Recipe Health'
    for r in find(dialog, Adw.ActionRow)))
check('report: the numbers arrive from the worker', shown)
titles = [g.get_title() for g in find(dialog, Adw.PreferencesGroup)
          if g.get_title() and g.get_visible()]
check('report: Quality, how people engaged, the stages, issues and '
      'suggestions follow the cook\'s own sections',
      titles[:2] == ['Last Cook', 'Left Out']
      and titles[2:] == ['Quality', 'How People Engaged',
                         'Posts at Each Stage', 'Issues', 'Suggestions'])
metrics_real = quality.assess_pages([BASE])
rows = {r.get_title(): r for r in find(dialog, Adw.ActionRow)}


def number_of(row):
    labels = find(row, Gtk.Label,
                  lambda w: 'numeric' in w.get_css_classes())
    return labels[0].get_label() if labels else ''


check('report: the score is the core\'s, with the caveat of what it '
      'measures under the title',
      number_of(rows['Latest Cook'])
      == '%.2f' % metrics_real['recipe_quality_score']
      and dialog.quality.quality_group.get_description()
      == quality.caveat_text(WORDS)
      and 'filters' in dialog.quality.quality_group.get_description())
names = quality.band_names(WORDS)
kitchen = metrics_real['kitchen']
counts = [number_of(rows[names[b]]) for b in quality.BANDS]
check('report: the four bands show their counts in words (not a colour)',
      counts == [('%d post' if kitchen['quality_distribution'][b] == 1
                  else '%d posts') % kitchen['quality_distribution'][b]
                 for b in quality.BANDS]
      and sum(int(c.split()[0]) for c in counts) == kitchen['total_toots'])
check('report: pantry, diet and kitchen show how many posts each had',
      [number_of(rows[t]) for t in ('Pantry', 'Diet', 'Kitchen')]
      == [str(metrics_real[k]['total_toots'])
          for k in ('pantry', 'diet', 'kitchen')])
analysed = real_analyze(recipe, [BASE])
reasons = {healthmod_text for healthmod_text in (
    health.reason_text(r, WORDS) for r in health.REASONS)}
issues = [r.get_title() for r in find(dialog.quality.issues_group, Adw.ActionRow)]
check('report: the issues are the health reasons in words (or none found)',
      issues and all(i in reasons or i in ('No Issues Found',
                                           'Not Enough Cooks Yet')
                     for i in issues)
      and (set(issues) & reasons
           == {health.reason_text(r, WORDS)
               for r in analysed.health.reasons}))
buttons = find(dialog.quality.suggestions_group, Gtk.Button,
               lambda b: b.get_label() == 'Apply')
expected = [r for r in analysed.recommendations[:6] if not r.manual]
check('report: each suggestion that has edits has an Apply button, with '
      'a name that says what it applies; a manual one is text only',
      len(buttons) == len(expected)
      and all((b.get_tooltip_text() or '').startswith('Apply: ')
              for b in buttons)
      and len(find(dialog.quality.suggestions_group, Adw.ActionRow))
      >= len(analysed.recommendations[:6]))
check('report: the analysis ran off the main thread',
      THREADS['analyze'] and all(t is not MAIN
                                 for t in THREADS['analyze'])
      and THREADS['analyze'][-1].name == 'feedchef-quality')
check('report: no row is only a colour: the status rows have words',
      all(r.get_title() for r in find(dialog, Adw.ActionRow)))
bars = find(dialog, Gtk.LevelBar)
check('report: the bars are one colour: no level offsets recolour them',
      bars and all(not b.get_offset_value(name)[0]
                   for b in bars for name in ('low', 'high', 'full')))
check('report: the latest cook and the health use the one score scale',
      health.assess(quality.summaries([BASE])).mean
      == metrics_real['recipe_quality_score'])
dialog.force_close()
pump()
dialog = None
rows = None
buttons = None

fallback = copy.deepcopy(BASE)
fallback['fresh_fallback'] = True
page2 = open_page(default_recipe('Faded'), lambda r, v, m, p: fallback)
page2.report_button.emit('clicked')
pump_until(lambda: dialogs(feedchefexplain.KitchenReportDialog))
dialog = dialogs(feedchefexplain.KitchenReportDialog)[-1]
pump_until(lambda: any(r.get_title() == 'Recipe Health'
                       for r in find(dialog, Adw.ActionRow)))
notice = 'Freshness Minimum Applied'
check('report: a cook with a freshness fallback page says so',
      notice in [r.get_title() for r in find(dialog, Adw.ActionRow)])
dialog.force_close()
pump()
check('report: without a fallback there is no such notice',
      notice not in [r.get_title() for r in find(
          feedchefexplain.KitchenReportDialog(
              [BASE], page.report_button and page.experiment.quality_context()),
          Adw.ActionRow)])
dialog = None
page2 = None
report = feedchefexplain.KitchenReportDialog(
    [BASE], page.experiment.quality_context())
refs = [report.weak_ref(), report.quality.weak_ref()]
report = None
check('report: a report dialog is freed with its Quality controller',
      all(freed(r) for r in refs))

# The page, its dialogs and the worker leave nothing behind.
del TOASTS[:]
ref = page.weak_ref()
window.set_child(None)
page = None
check('page: a recipe page with the quality layer is freed once closed',
      freed(ref))
listing = feedcheflist.FeedChefListPage(Client(), account)
refs = [listing.weak_ref(), listing.health.weak_ref()]
listing = None
check('list: a recipe list is freed with its health controller',
      all(freed(r) for r in refs))

# --- 2. Apply, Undo ----------------------------------------------------------

reset()
history.analyze = watch('analyze', stub_analyze)
recipe = make_recipe()
original = stored(recipe)
storage.save_recipe(recipe)
page = open_page(storage.get_recipe_by_id('opt-1'))
recs = [r for r in advice.recommend(page.recipe, SYN)[:6] if not r.manual]
check('apply: the stubbed metrics give a suggestion with edits', bool(recs))
rec = recs[0]
engines_before = len(ENGINES)
page.report_button.emit('clicked')
pump_until(lambda: dialogs(feedchefexplain.KitchenReportDialog))
dialog = dialogs(feedchefexplain.KitchenReportDialog)[-1]
pump_until(lambda: find(dialog.quality.suggestions_group, Gtk.Button,
                        lambda b: b.get_label() == 'Apply'))
titles = [r.get_title() for r in find(dialog.quality.suggestions_group,
                                       Adw.ActionRow)]
check('apply: a high-severity suggestion says so in words, not by its icon',
      all(any(t.startswith('Important: ') for t in titles)
          for _x in [0] if any(r.severity == 'high' for r in recs))
      and not any(t.startswith('Important: ') for t in titles
                  if 'Try a Change' in t))
dialog.activate_action('quality.apply', GLib.Variant.new_string(rec.id))
pump(10)
applied = rec.apply(make_recipe()).recipe
check('apply: one click saves the recipe with the change and cooks it again',
      stored(page.recipe) == stored(applied)
      and stored(storage.get_recipe_by_id('opt-1')) == stored(applied)
      and len(ENGINES) > engines_before)
apply_button = dialog.quality._quality_buttons[rec.id]
check('apply: the report stays open, its button says Applied, and the Undo '
      'toast is the dialog\'s own and names the setting',
      dialogs(feedchefexplain.KitchenReportDialog)
      and apply_button.get_label() == 'Applied'
      and not apply_button.get_sensitive()
      and TOASTS and TOASTS[-1].text.startswith('Applied “')
      and TOASTS[-1].widget is dialog and TOASTS[-1].undo is not None)
check('apply: Apply is not the suggested action',
      'suggested-action' not in apply_button.get_css_classes())
toasts_before, engines_before = len(TOASTS), len(ENGINES)
dialog.activate_action('quality.apply', GLib.Variant.new_string(rec.id))
pump(10)
check('apply: the same change twice is a no-op: no toast, no new cook',
      len(TOASTS) == toasts_before and len(ENGINES) == engines_before)
TOASTS[-1].undo()
pump(10)
check('apply: Undo makes the button usable again',
      apply_button.get_label() == 'Apply' and apply_button.get_sensitive())
check('apply: Undo puts the recipe back exactly, on screen and in storage',
      stored(page.recipe) == original
      and stored(storage.get_recipe_by_id('opt-1')) == original)
dialog.force_close()
pump()
storage.delete_recipe('opt-1')
toasts_before = len(TOASTS)
check('apply: a recipe deleted since is not brought back by Apply, and the '
      'reader is told',
      page.experiment.apply_recommendation(rec) is False
      and storage.get_recipe_by_id('opt-1') is None
      and TOASTS[-1].text == 'This recipe no longer exists')
proposed = optimizer.propose(make_recipe(), advice.recommend(
    make_recipe(), SYN))
check('apply: no experiment sidecar is written for a recipe that is gone, '
      'and one is for a recipe that is there',
      storage.save_optimizer('opt-1', proposed, require_recipe=True)
      is False and storage.load_optimizer('opt-1') is None
      and (storage.save_recipe(make_recipe()) or True)
      and storage.save_optimizer('opt-1', proposed, require_recipe=True)
      and storage.load_optimizer('opt-1') is not None)
storage.delete_recipe('opt-1')
dialog = None

# --- 3. the list's health badges ---------------------------------------------

reset()
history.analyze = real_analyze
for number, (rid, kind, cooks) in enumerate((
        ('hl-bad', 'bad', 10), ('hl-good', 'good', 4))):
    found = default_recipe(rid)
    found.id = rid
    storage.save_recipe(found)
    for cook in range(cooks):
        write_cook(rid, 200 * (number + 1) + cook, kind)
none = default_recipe('hl-none')
none.id = 'hl-none'
storage.save_recipe(none)
del THREADS['health_map'][:]
listing = feedcheflist.FeedChefListPage(Client(), account)
window.set_child(listing)
check('badge: before the worker answers, no badge claims anything',
      all(not b.get_visible() for b in listing.health.badges.values()))
pump_until(lambda: 'hl-bad' in listing.health.badges)
settle()


def badge(rid):
    found = listing.health.badges.get(rid)
    return found.label.get_label() if found is not None \
        and found.get_visible() else None


check('badge: Needs Attention for the recipe whose last cooks did badly, '
      'Working Well for the one that did well, none for a recipe never '
      'cooked',
      badge('hl-bad') == 'Needs Attention'
      and badge('hl-good') == 'Working Well' and badge('hl-none') is None)
check('badge: it is an icon and words, with the reasons in a tooltip',
      listing.health.badges['hl-bad'].icon.get_icon_name()
      == 'dialog-warning-symbolic'
      and listing.health.badges['hl-good'].icon.get_icon_name()
      == 'object-select-symbolic'
      and listing.health.badges['hl-bad'].get_tooltip_text())
check('badge: it was computed on the worker, not the main thread',
      THREADS['health_map']
      and all(t is not MAIN for t in THREADS['health_map']))
before = len(THREADS['health_map'])
again = feedcheflist.FeedChefListPage(Client(), account)
check('badge: a list shown again reads the cache, with no new job',
      again.health.badges['hl-bad'].get_visible()
      and len(THREADS['health_map']) == before)
window.set_child(None)
again = listing = None

# A corrupt log blanks only its recipe's badge, and says so once; the folder
# is read once for all the recipes.
import logging  # noqa: E402

for rid, text in (('hl-corrupt', '{{{ not json'),
                  ('hl-shape', json.dumps({'audit': {'posts': 5}}))):
    found = default_recipe(rid)
    found.id = rid
    storage.save_recipe(found)
    for number in range(3):
        path = os.path.join(LOGS, 'session_%d_%s__c%08x_%06x.json' % (
            1700000900000 + number, rid, 900 + number, 900 + number))
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
found = default_recipe('hl-boom')
found.id = 'hl-boom'
storage.save_recipe(found)
write_cook('hl-boom', 950, 'good')
boom = make_record(951, 'good')
boom['cook_id'] = 'boom'
with open(os.path.join(LOGS, 'session_1700000951000_hl-boom__c%08x_%06x.json'
                       % (951, 951)), 'w', encoding='utf-8') as handle:
    json.dump({'audit': boom}, handle)
real_summaries = quality.summaries


def exploding(records):
    if any(r.get('cook_id') == 'boom' for r in records):
        raise ValueError('an odd record')
    return real_summaries(records)


quality.summaries = exploding
SAID = []


class Catch(logging.Handler):
    def emit(self, record):
        SAID.append(record.getMessage())


catcher = Catch()
logging.getLogger('babble.feedchef').addHandler(catcher)
scans = []
real_cooks = logmod._cooks
logmod._cooks = lambda directory: scans.append(directory) or real_cooks(
    directory)
ids = ['hl-bad', 'hl-good', 'hl-corrupt', 'hl-shape', 'hl-boom', 'hl-none']
found = real_map(ids)
real_map(ids)
logmod._cooks = real_cooks
quality.summaries = real_summaries
logging.getLogger('babble.feedchef').removeHandler(catcher)
check('badge: one recipe whose logs are corrupt, oddly shaped or break the '
      'analysis is Unknown, and never takes the others with it',
      list(found) == ids
      and found['hl-bad'].state == 'needs_attention'
      and found['hl-good'].state == 'working_well'
      and all(found[i].state == 'unknown'
              for i in ('hl-corrupt', 'hl-shape', 'hl-boom', 'hl-none')))
check('badge: a recipe that cannot be judged is said once, not at every '
      'refresh',
      len([s for s in SAID if 'hl-boom' in s]) == 1)
check('badge: the log folder is read once per refresh for all the recipes',
      len(scans) == 2)

listing = feedcheflist.FeedChefListPage(Client(), account)
window.set_child(listing)
pump_until(lambda: 'hl-bad' in listing.health.badges)
badge = listing.health.badges['hl-bad']
check('badge: it is an image with a name for a screen reader',
      badge.get_accessible_role() == Gtk.AccessibleRole.IMG
      and badge.label.get_visible())
listing.compact = True
check('badge: narrow, it is the icon alone, with its words as the tooltip',
      not badge.label.get_visible() and badge.icon.get_visible()
      and badge.get_tooltip_text().startswith('Needs Attention'))
listing.compact = False
check('badge: wide again, the words are back', badge.label.get_visible())
window.set_child(None)
listing = badge = None

# --- 4. the banner at the alert's boundary, the toast once -------------------

reset()
rid = 'ban-1'
found = default_recipe('Pets')
found.id = rid
storage.save_recipe(found)
for cook in range(9):
    write_cook(rid, 300 + cook, 'bad')
page = open_page(storage.get_recipe_by_id(rid),
                 lambda r, v, m, p: None)
check('banner: nine bad cooks need attention but are no alert yet',
      page.experiment._health.state == 'needs_attention'
      and not page.experiment._health.alert
      and not page.experiment.health_banner.get_revealed())
write_cook(rid, 309, 'bad')
page.experiment.request_health()
pump_until(lambda: page.experiment.health_banner.get_revealed())
check('banner: the tenth turns it on, with the words and the button',
      page.experiment.health_banner.get_revealed()
      and page.experiment.health_banner.get_title()
      == 'This recipe is getting less engagement'
      and page.experiment.health_banner.get_button_label() == 'Improve…')
page.experiment.health_banner.emit('button-clicked')
pump_until(lambda: dialogs(feedchefexplain.KitchenReportDialog))
dialog = dialogs(feedchefexplain.KitchenReportDialog)[-1]
pump_until(lambda: any(r.get_title() == 'Recipe Health'
                       for r in find(dialog, Adw.ActionRow)))
check('banner: Improve… opens the Quality section, which says the recipe '
      'needs attention',
      dialog.get_title() == 'Improve This Recipe'
      and any(r.get_title() == 'The score has been low over the last cooks'
              for r in find(dialog.quality.issues_group, Adw.ActionRow)))
dialog.force_close()
pump()
dialog = None

# The proactive toast: a recipe that was working and now needs attention,
# once per session.
reset()
rid = 'ban-2'
found = default_recipe('Pets')
found.id = rid
storage.save_recipe(found)
for cook in range(4):
    write_cook(rid, 400 + cook, 'good')
page_b = open_page(storage.get_recipe_by_id(rid), lambda r, v, m, p: None)
check('toast: first the recipe is working well, and says nothing',
      page_b.experiment._health.state == 'working_well' and not TOASTS)
for cook in range(10):
    write_cook(rid, 500 + cook, 'bad')
page_b.experiment.request_health()
pump_until(lambda: TOASTS)
check('toast: it names the recipe, with a button to the suggestions',
      TOASTS and TOASTS[-1].text
      == '“Pets” needs attention'
      and TOASTS[-1].undo_label == '_See Suggestions'
      and TOASTS[-1].undo is not None)
count = len(TOASTS)
page_b.experiment.request_health()
settle()
check('toast: not again in the same session',
      len(TOASTS) == count)
TOASTS[-1].undo()
pump_until(lambda: dialogs(feedchefexplain.KitchenReportDialog))
check('toast: the button opens the suggestions',
      bool(dialogs(feedchefexplain.KitchenReportDialog)))
for item in dialogs(feedchefexplain.KitchenReportDialog):
    item.force_close()
pump()
page = None
page_b = None


names = [t.name for t in threading.enumerate()]
check('threads: the quality worker is one daemon thread',
      names.count('feedchef-quality') == 1
      and [t.daemon for t in threading.enumerate()
           if t.name == 'feedchef-quality'] == [True])

print('%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
