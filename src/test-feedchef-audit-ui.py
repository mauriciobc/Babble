#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's transparency dialogs over a real audit record: the post menu
has Why This Post?, its dialog shows the sections and numbers the record
holds (and a status page for a post with no record), the Kitchen Report
counts match the record, Copy Report carries counts, verdicts, a hash and
dates and nothing of a post, Clear Audit Logs asks first and clears only on
the answer, and both dialogs are freed once closed. The engine is a fake
that hands out the record the real Recorder made. Exits 77 (skip) without a
display."""

import os
import tempfile

# Never the user's real dconf, data or state.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
_TMP = tempfile.mkdtemp(prefix='babble-feedchef-audit-ui-')
for _name in ('DATA', 'STATE', 'CACHE'):
    os.environ['XDG_%s_HOME' % _name] = os.path.join(_TMP, _name.lower())

import gc  # noqa: E402
import random  # noqa: E402
import sys  # noqa: E402
from types import SimpleNamespace  # noqa: E402

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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(srcdir)),
                                'bench'))
sys.path.insert(0, srcdir)

from feedchefaudit import fuzz  # noqa: E402
from src import feedback, feedchefexplain, feedchefpages  # noqa: E402
from src.feedchef import auditreport  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import storage as storagemod  # noqa: E402
from src.feedchef.engine import FeedResult  # noqa: E402
from src.feedchef.recipe import default_recipe  # noqa: E402
from src.statusmenu import StatusMenuButton  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402

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


def pump_until(condition, seconds=5.0):
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(seconds * 1e6)
    while not condition() and GLib.get_monotonic_time() < deadline:
        while context.iteration(False):
            pass
        GLib.usleep(2000)
    return condition()


def freed(ref, attempts=5):
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


def rows_of(dialog):
    """{group title: {row title: (subtitle, value)}} of a dialog's page."""
    found = {}
    for group in find(dialog, Adw.PreferencesGroup,
                      lambda g: bool(g.get_title())):
        rows = {}
        for row in find(group, Adw.ActionRow):
            labels = find(row, Gtk.Label,
                          lambda w: 'numeric' in w.get_css_classes())
            rows[row.get_title()] = (row.get_subtitle(),
                                     labels[0].get_label() if labels
                                     else '')
        found[group.get_title()] = rows
    return found


# --- a real record: the Recorder's, over the fuzz world -------------------

logmod.set_root(os.path.join(_TMP, 'logs'))
record = results = None
for seed in range(200):
    rng = random.Random(seed)
    recipe = fuzz.random_recipe(rng)
    recipe.meta.name = 'Secret Recipe Name'
    world = fuzz.World(rng, recipe)
    results = fuzz.cook(world, recipe, seed, pages=1)
    record = results[0].logger.record
    kinds = {m.type for m in recipe.kitchen.modifiers}
    if record and record['summary']['selected'] >= 3 \
            and record['summary']['by_reason'] \
            and kinds & {'freshness', 'trending', 'sentiment_boost'}:
        break
check('a record with removals, scores and a modifier was cooked',
      record is not None and record['summary']['by_reason']
      and record['summary']['selected'] >= 3)
by_id = {entry['id']: entry for entry in record['posts']}
shown_id = record['outputs']['order'][1]
entry = by_id[shown_id]
removed = next(e for e in record['posts'] if e['removal'])


class FakeEngine:
    """Answers a cook with one page and the record of it."""

    def __init__(self, record):
        self.record = record

    def init_preload(self):
        pass

    def cancel(self):
        pass

    def shutdown(self):
        pass

    def process_feed(self, recipe, *, on_done=None, **_kwargs):
        record = self.record
        ids = record['outputs']['order'] if record else ['00001']

        def deliver():
            on_done(FeedResult(
                items=[{'id': sid, 'content': '<p>x</p>', 'tags': [],
                        'account': {'acct': 'u%s' % sid, 'id': '1'},
                        'created_at': '2026-01-01T00:00:00.000Z'}
                       for sid in ids],
                next_page_max_id=None, has_more=False,
                scope_oldest_id=None,
                logger=SimpleNamespace(record=record)))
            return GLib.SOURCE_REMOVE
        GLib.idle_add(deliver)
        return 'fake'


class Client:
    token = None
    instance_info = None
    instance = 'https://example.org'


Adw.init()
storagemod.set_root(os.path.join(_TMP, 'recipes'))
feedback.show_toast = lambda *_a, **_k: None
ENGINE = {'record': record}
feedchefpages.make_engine = lambda *_args: FakeEngine(ENGINE['record'])

# The dialogs the page makes are found in the toplevels (nothing else holds
# them) and are watched with a GObject weak ref, to see them freed.
MADE = []
FINALIZED = set()
BASES = (feedchefexplain.WhyThisPostDialog,
         feedchefexplain.KitchenReportDialog)


def watched(base):
    class Watched(base):
        def __init__(self, *args):
            super().__init__(*args)
            number = len(MADE)
            MADE.append(number)
            self.weak_ref(FINALIZED.add, number)
    return Watched


feedchefexplain.WhyThisPostDialog = watched(BASES[0])
feedchefexplain.KitchenReportDialog = watched(BASES[1])

account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
window = Gtk.Window(default_width=900, default_height=700)
window.present()


def open_page(engine_record):
    ENGINE['record'] = engine_record
    page = feedchefpages.FeedChefTimelinePage(
        Client(), account, default_recipe('Cook'))
    navview = Adw.NavigationView()
    navview.add(Adw.NavigationPage(title='Cook', child=page))
    window.set_child(navview)
    pump_until(lambda: page.model.get_n_items() > 0)
    return page


def last_dialog():
    """The dialog now open (None when there is none)."""
    found = []

    def walk(widget):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, BASES):
                found.append(child)
            walk(child)
            child = child.get_next_sibling()
    for top in Gtk.Window.list_toplevels():
        walk(top)
    return found[-1] if found else None


def finalized(number):
    """Whether dialog *number* of MADE is finalized (the caller has let go
    of it first)."""
    for _attempt in range(5):
        gc.collect()
        pump(3)
        if number in FINALIZED:
            return True
    return number in FINALIZED


page = open_page(record)
check('the page shows the cooked posts',
      page.model.get_n_items() == len(record['outputs']['order']))

# --- the post menu --------------------------------------------------------

button = StatusMenuButton()
button.bind(StatusItem({'id': shown_id, 'content': '<p>x</p>', 'tags': [],
                        'account': {'acct': 'u', 'id': '1'},
                        'created_at': '2026-01-01T00:00:00.000Z'}),
            Client())
page.get_child().append(button)
button.prepare()
menu = button.get_menu_model()
labels = {}
for number in range(menu.get_n_items()):
    inner = menu.get_item_link(number, 'section')
    for index in range(inner.get_n_items() if inner else 0):
        label = inner.get_item_attribute_value(index, 'label', None)
        action = inner.get_item_attribute_value(index, 'action', None)
        labels[label.get_string()] = action.get_string()
check('menu: Why This Post? is in the post menu, with More Like This',
      labels.get('_Why This Post?', '').startswith('page.')
      and 'More _Like This' in labels)
keys = [label[label.index('_') + 1].lower() for label in labels
        if '_' in label and label.index('_') + 1 < len(label)
        and label[label.index('_') + 1] != '_']
check('menu: no access key is used twice', len(keys) == len(set(keys)))

# --- Why This Post? -------------------------------------------------------

button.activate_action(labels['_Why This Post?'], None)
pump()
dialog = last_dialog()
sections = rows_of(dialog)
core = [t for t in sections if t in (
    'Where It Came From', 'Checks It Passed', 'How It Was Ranked')]
check('why: it has the three sections, in words, and a title',
      dialog.get_title() == 'Why This Post?'
      and core == ['Where It Came From', 'Checks It Passed',
                   'How It Was Ranked']
      and set(sections) <= {'Where It Came From', 'About This Post',
                            'Checks It Passed', 'Not Checked',
                            'How It Was Ranked'})
passed = {t for rule, t in auditreport.rule_titles(
    auditreport.ENGLISH).items() if entry['rules'].get(rule) == 'pass'}
check('why: every row under Checks It Passed is a rule the record passed',
      {t for t in sections['Checks It Passed']
       if t not in ('Your Own Post', 'From Someone You Follow',
                    'Allowed by the Network Rule', 'Allowed by Your Filters',
                    'No Rules Applied', 'No Checks Passed')} == passed)
check('why: a language is named, never shown as its code',
      not entry['facts'].get('lang')
      or (sections['About This Post']['Language'][1]
          not in ('', entry['facts']['lang'])))
check('why: Not Checked rows say why and have no empty value',
      all(sub and not value for sub, value in
          sections.get('Not Checked', {}).values()))
ranking = sections.get('How It Was Ranked', {})
check('why: Reactions and Score explain themselves',
      ranking['Reactions'][0] == 'Favorites, boosts and replies, weighted'
      and ranking['Score'][0] == 'Higher scores are shown first')
score = entry['score']
facts = entry['facts']
check('why: Reactions names the counts and shows the base',
      ranking.get('Reactions', ('', ''))[1]
      == '%.1f' % score['base'])
reactions = ranking.get('Reaction Counts', ('',))[0]
check('why: the counts are in plain words',
      all(text in reactions for text in (
          ['%d favorite' % facts['fav']] if facts['fav'] else [])
          + (['%d boost' % facts['reblog']] if facts['reblog'] else [])
          + (['%d repl' % facts['reply']] if facts['reply'] else []))
      and ('No reactions yet' in reactions
           or any(ch.isdigit() for ch in reactions)))
check('why: the score and the position match the record',
      ranking.get('Score', ('', ''))[1] == '%.1f' % score['final']
      and ranking.get('Position', ('', ''))[1]
      == '%d of %d' % (entry['rank'] + 1, record['summary']['selected']))
types = {m['type'] for m in record['recipe']['snapshot']['kitchen']
         ['modifiers']}
for kind, title in (('freshness', 'Freshness'),
                    ('sentiment_boost', 'Positive Tone'),
                    ('trending', 'Trending')):
    check('why: %s has a row exactly when the recipe uses it' % title,
          (title in ranking) == (kind in types or score[
              {'freshness': 'fresh', 'sentiment_boost': 'sent',
               'trending': 'trend'}[kind]] != 1.0))
if 'Freshness' in ranking:
    check('why: a factor reads as a multiplier',
          ranking['Freshness'][1].startswith('× '))
origin = sections['Where It Came From']['Found Through'][0]
check('why: the origin is in plain words',
      origin and ':' not in origin and 'scope' not in origin)
all_text = ' '.join(
    '%s %s %s' % (t, s, v) for rows in sections.values()
    for t, (s, v) in rows.items())
check('why: no engine jargon reaches the reader',
      not any(word in all_text.lower() for word in (
          'vader', 'leia', 'heat', 'guard', 'contract', 'pantry_',
          'diet:')))
number = MADE[-1]
dialog.force_close()
pump()
del dialog
check('why: the dialog is freed once closed', finalized(number))
check('why: no dialog is left open', last_dialog() is None)

page._why_this_post({'id': 'no-such-post'})
pump()
dialog = last_dialog()
statuses = find(dialog, Adw.StatusPage)
check('why: a post with no record shows a status page',
      dialog.found is False and statuses
      and statuses[0].get_title() == 'No Record for This Post'
      and statuses[0].get_description()
      == 'This post isn’t in the record Feed Chef keeps while this '
         'timeline is open.'
      and statuses[0].get_icon_name() == 'document-properties-symbolic'
      and not find(dialog, Adw.PreferencesGroup))
number = MADE[-1]
dialog.force_close()
pump()
del dialog, statuses
check('why: the dialog without a record is freed too', finalized(number))

# A removed post says why, and a safety check is called by that name.
sections = auditreport.explain_post(removed, record)
check('report: a removed post has a reason in place of a rank',
      [s.title for s in sections][-1] == 'Why It Was Removed'
      and sections[-1].rows[0].subtitle
      == auditreport.reason_text(removed['removal']['reason']))
check('report: a guard drop is "removed by a safety check"',
      auditreport.reason_text('guard:min_content_length')
      == 'Removed by a safety check')
check('report: an unknown reason is still plain words',
      auditreport.reason_text('???') == 'Removed for another reason')

# --- Kitchen Report -------------------------------------------------------

check('report: the header has a Kitchen Report button with a real icon',
      page.report_button in page.header_widgets
      and page.report_button.get_icon_name()
      == 'document-properties-symbolic'
      and Gtk.IconTheme.get_for_display(window.get_display()).has_icon(
          'document-properties-symbolic')
      and page.report_button.get_tooltip_text() == 'Kitchen Report')
page.report_button.emit('clicked')
pump()
dialog = last_dialog()
sections = rows_of(dialog)
summary = record['summary']
check('report: posts gathered and shown match the record',
      list(sections)[:2] == ['Last Cook', 'Left Out']
      and sections['Last Cook']['Posts gathered'][1]
      == str(summary['raw_fetched'])
      and sections['Last Cook']['Posts shown'][1]
      == str(summary['selected']))
left = sum(int(v) for t, (s, v) in sections['Left Out'].items()
           if v.isdigit() and t != 'Seen more than once')
check('report: the reasons add up to the posts removed',
      left == sum(summary['by_reason'].values()))
check('report: each reason is in plain words',
      all(auditreport.reason_text(reason) in sections['Left Out']
          for reason in summary['by_reason']))
group = find(dialog, Adw.PreferencesGroup,
             lambda g: g.get_title() == 'Last Cook')[0]
check('report: the recipe\'s settings are one line under Last Cook',
      group.get_description() == auditreport.settings_line(
          record['recipe']['snapshot']))
titles = [r.get_title() for r in find(dialog, Adw.ButtonRow)]
check('report: Copy Report, Open Logs Folder and Clear Audit Logs',
      titles == ['_Copy Report', 'Open _Logs Folder', 'Clear _Audit Logs'])

icons = Gtk.IconTheme.get_for_display(window.get_display())
check('report: the icons the dialogs use resolve in the theme',
      all(icons.has_icon(name) for name in (
          'document-properties-symbolic', 'folder-symbolic',
          'user-trash-symbolic', 'edit-copy-symbolic')))
check('report: Clear sits in a group of its own with a trash icon, '
      'Open Logs Folder has a folder icon',
      dialog.clear_row.get_start_icon_name() == 'user-trash-symbolic'
      and dialog.folder_row.get_start_icon_name() == 'folder-symbolic'
      and dialog.clear_row.get_ancestor(Adw.PreferencesGroup)
      is not dialog.copy_row.get_ancestor(Adw.PreferencesGroup))
check('report: Open Logs Folder is sensitive when the folder exists',
      dialog.folder_row.get_sensitive() is True)
logmod.set_root(os.path.join(_TMP, 'no-logs-at-all'))
bare = feedchefexplain.KitchenReportDialog([record])
check('report: Open Logs Folder is insensitive when there is no folder',
      bare.folder_row.get_sensitive() is False)
bare.force_close()
pump()
del bare
logmod.set_root(os.path.join(_TMP, 'logs'))

# Copy Report: counts, verdicts, a hash and dates, and nothing of a post.
text = dialog.report.text
sidecar = results[0].logger.audit.sidecar
secrets = [e['id'] for e in record['posts']] \
    + [e['cid'] for e in record['posts']] \
    + [str(e['facts'].get('author_id') or '') for e in record['posts']] \
    + [str(e['facts'].get('booster_id') or '') for e in record['posts']] \
    + [row['text'][:12] for row in sidecar.values()
       if len(row.get('text') or '') >= 12] \
    + ['Secret Recipe Name', 'ex.ample', '@', 'http']
secrets = [s for s in secrets if len(s) >= 5 or s in ('@', 'http')]
leaked = [s for s in secrets if s in text]
check('copy: nothing of a post, author or recipe name is in the text '
      '(%d things looked for)' % len(secrets), secrets and not leaked)
check('copy: it has the counts, the hash and the dates',
      'Posts gathered: %d' % summary['raw_fetched'] in text
      and 'Posts shown: %d' % summary['selected'] in text
      and record['recipe']['hash'][:12] in text
      and record['clock']['now_utc'] in text)
dialog.copy_row.emit('activated')
pump()
clipboard = dialog.get_display().get_clipboard()
got = []
clipboard.read_text_async(None, lambda c, r: got.append(c.read_text_finish(r)))
check('copy: the clipboard holds the report',
      pump_until(lambda: got) and got[0] == text)

# Open Logs Folder: the log folder, or a word when there is none.
LAUNCHED = []
feedchefexplain.launch_folder = lambda _p, path, _cb: LAUNCHED.append(path)
logmod.set_root(os.path.join(_TMP, 'no-logs-yet'))
dialog.folder_row.emit('activated')
check('folder: with no logs yet it says so and opens nothing',
      not LAUNCHED)
logmod.set_root(os.path.join(_TMP, 'logs'))
dialog.folder_row.emit('activated')
check('folder: it opens the audit log folder',
      LAUNCHED == [logmod.log_dir()])

# Clear Audit Logs: asks first; Cancel clears nothing, Clear clears.
fuzz.cook(fuzz.World(random.Random(1), recipe), recipe, 1, pages=1)
real_clear = logmod.clear_all
CLEARS = []
logmod.clear_all = lambda: CLEARS.append(1) or real_clear()


def logs():
    return [n for n in os.listdir(logmod.log_dir())
            if n.endswith('.json')] if os.path.isdir(logmod.log_dir()) else []


check('clear: there are logs to clear', bool(logs()))
dialog.clear_row.emit('activated')
pump()
alert = dialog.alert
check('clear: it asks "Clear Audit Logs?", Cancel by default, '
      'with a destructive Clear',
      alert.get_heading() == 'Clear Audit Logs?'
      and alert.get_body() == (
          'This deletes the audit history Feed Chef keeps on this computer, '
          'for all your recipes. Your recipes and timelines are not '
          'affected.')
      and alert.get_default_response() == 'cancel'
      and alert.get_response_label('cancel') == '_Cancel'
      and alert.get_response_label('ok') == '_Clear Logs'
      and alert.get_response_appearance('ok')
      == Adw.ResponseAppearance.DESTRUCTIVE and not CLEARS)
alert.emit('response', 'cancel')
alert.force_close()
pump()
check('clear: Cancel clears nothing', not CLEARS and bool(logs()))
dialog.clear_row.emit('activated')
pump()
alert = dialog.alert
alert.emit('response', 'ok')
alert.force_close()
pump()
check('clear: Clear calls clear_all once and the logs are gone',
      CLEARS == [1] and not logs())
statuses = find(dialog, Adw.StatusPage)
check('clear: the report is swapped for "No Report Yet", no stale counts',
      statuses and statuses[0].get_title() == 'No Report Yet'
      and not find(dialog, Adw.ButtonRow)
      and not find(dialog, Adw.PreferencesGroup)
      and dialog.report.pages == 0)
logmod.clear_all = real_clear

number = MADE[-1]
dialog.force_close()
pump()
del dialog, alert, group
check('report: the dialog is freed once closed', finalized(number))

# No cook yet: a status page.
empty = open_page(None)
empty.report_button.emit('clicked')
pump()
dialog = last_dialog()
statuses = find(dialog, Adw.StatusPage)
check('report: with no cook it is a status page "No Report Yet"',
      statuses and statuses[0].get_title() == 'No Report Yet'
      and statuses[0].get_description()
      == 'Load some posts in this timeline, and a summary will appear here.'
      and statuses[0].get_icon_name() == 'document-properties-symbolic'
      and not find(dialog, Adw.ButtonRow))
dialog.force_close()
pump()

# --- the words of a record that is not what it should be ------------------

odd = {'posts': [{'id': 'x'}], 'summary': None, 'recipe': None}
try:
    auditreport.explain_post({'id': 'x', 'rules': None, 'facts': None,
                              'follow': None, 'score': None}, odd)
    auditreport.cook_report([odd, {}, {'outcome': {'status': 'ok'}}])
    sturdy = True
except Exception:
    sturdy = False
check('report: a thin record is worded, not an exception', sturdy)

print('%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
