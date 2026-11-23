#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef audit: the record the engine writes beside each decision log.

Display-free. The audit only observes, so the same world cooked with it on
and off must give the same posts, order, scores and counts; every record
the fuzz writes must replay identically; retention is per recipe (the last
20 cooks of each, under a 50 MB cap) and Clear Audit Logs works; the
sidecar that holds the text is private; an export carries counts,
verdicts, hashes and dates only; and capture costs at most 5 ms (p95) per
40-post page.
"""

import json
import os
import random
import re
import stat
import statistics
import sys
import tempfile
import time

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))
sys.path.insert(0, os.path.join(os.path.dirname(srcdir), 'bench'))

from feedchefaudit import evaluate as evalmod  # noqa: E402
from feedchefaudit import fuzz  # noqa: E402
from feedchefaudit import replay as replaymod  # noqa: E402
from feedchefaudit.collect import collect  # noqa: E402
from feedchefaudit.records import expand, load  # noqa: E402
from src.feedchef import audit as auditmod  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402

CASES = int(os.environ.get('FEEDCHEF_AUDIT_CASES', '120'))
TIMED = int(os.environ.get('FEEDCHEF_AUDIT_TIMED', '60'))
failures = 0
TMP = tempfile.mkdtemp(prefix='feedchef-audit-')
reachmod.set_root(os.path.join(TMP, 'reach'))


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


def fresh_logs(name):
    root = os.path.join(TMP, name)
    logmod.set_root(root)
    return root


def hide_some(status):
    return int(status['id']) % 7 == 0


def page_view(result):
    return (result.items, result.score_map, result.author_counts,
            result.has_more, result.scope_oldest_id,
            result.next_page_max_id, result.kitchen_meta, result.guard)


# --- neutrality: the same world with the audit on and off ----------------

fresh_logs('neutral')
different = []
pages_seen = posts_seen = hidden_posts = 0
for case in range(CASES):
    rng = random.Random(1000 + case)
    recipe = fuzz.random_recipe(rng)
    world = fuzz.World(rng, recipe)
    on = fuzz.cook(world, recipe, case, now=fuzz.NOW, hide=hide_some)
    off = fuzz.cook(world, recipe, case, audit=False, now=fuzz.NOW,
                    hide=hide_some)
    if len(on) != len(off) or any(
            page_view(a) != page_view(b) for a, b in zip(on, off, strict=True)):
        different.append(1000 + case)
    pages_seen += len(on)
    posts_seen += sum(len(r.items) for r in on)
    hidden_posts += sum(
        e['removal'] is not None and e['removal']['reason'] == 'viewer_hide'
        for r in on for e in r.logger.record['posts'])
check('audit on and off agree on items, order, scores, counts and meta '
      '(%d cooks, %d pages, %d posts shown, %d viewer hides)%s'
      % (CASES, pages_seen, posts_seen, hidden_posts,
         ' -- seeds %r' % different[:5] if different else ''),
      not different and posts_seen > 1000 and hidden_posts > 0)
check('a cook with the audit off has no audit record',
      off[0].logger.record is None)

# --- every record the fuzz wrote replays identically ----------------------

logs = [load(path) for path in expand([fresh_logs('neutral')])]
audit_logs = [item for item in logs if item.record]
results = [replaymod.replay(item) for item in audit_logs]
bad = [(item.record['cook_id'], r.detail)
       for item, r in zip(audit_logs, results, strict=True)
       if r.status != 'identical']
check('every fuzz record replays identically (%d of %d)'
      % (len(results) - len(bad), len(results)),
      not bad and len(results) >= CASES)
if bad:
    print('   ', bad[:3])
tally = evalmod.Tally()
for pages in evalmod.group_cooks(audit_logs).values():
    tally.merge(evalmod.evaluate_cook(pages))
check('every fuzz cook passes every recipe check (%s)' % ', '.join(
    '%s %d' % (rule, tally.checked[rule]) for rule in evalmod.RULES),
    tally.ok() and all(tally.checked[rule] for rule in evalmod.RULES))
record = audit_logs[0].record
check('the record is schema 2, from engine revision %d, app "dev"'
      % auditmod.ENGINE_REV,
      record['schema'] == 2 and record['engine_rev'] == auditmod.ENGINE_REV
      and record['app']['version'] == 'dev')
by_reason = {}
for item in audit_logs:
    summary = item.record['summary']
    check_sum = summary['selected'] + sum(summary['by_reason'].values())
    if check_sum != summary['pantry_in']:
        by_reason['sum'] = item.record['cook_id']
    for reason in summary['by_reason']:
        if not auditmod.is_reason(reason):
            by_reason[reason] = 1
check('every post is selected or removed once, for a reason of the closed '
      'set', not by_reason)

# --- the sidecar is private; an export carries no text, handle or id ------

some = audit_logs[0]
mode = stat.S_IMODE(os.stat(auditmod.sidecar_path(some.path)).st_mode)
check('the text sidecar is mode 0600 (%o)' % mode, mode == 0o600)
dir_mode = stat.S_IMODE(os.stat(os.path.dirname(some.path)).st_mode)
check('the log folder is 0700 (%o)' % dir_mode, dir_mode == 0o700)
export = os.path.join(TMP, 'export.json')
lines = []
collect([fresh_logs('neutral')], json_path=export, out=lines.append)
with open(export, encoding='utf-8') as handle:
    exported = handle.read()
secrets = set()
for item in audit_logs:
    for entry in item.record['posts']:
        secrets.update((entry['id'], entry['cid']))
        for key in ('author_id', 'booster_id'):
            if entry['facts'][key]:
                secrets.add('"%s"' % entry['facts'][key])
    secrets.update((item.record['recipe']['id'], item.record['cook_id'],
                    item.record['session_id']))
    for row in (item.sidecar or {}).values():
        secrets.add(row['text'][:20])
# A post id is a run of digits that could sit inside a hex hash by
# chance: it counts as leaked only as a token of its own.
leaked = sorted(
    s for s in secrets if s and re.search(
        r'(?<![0-9A-Za-z])%s(?![0-9A-Za-z])' % re.escape(s), exported))
check('the export (%d bytes) carries none of %d ids, handles or texts%s'
      % (len(exported), len(secrets), ' -- %r' % leaked[:3] if leaked
         else ''), not leaked)
exports = json.loads(exported)
check('the export has the recipe hash, dates, counts and verdicts',
      exports['recipes'] and all(
          re.fullmatch(r'[0-9a-f]{64}', row['recipe_hash'])
          and re.fullmatch(r'\d{4}-\d\d-\d\d', row['last_day'])
          and row['verdicts']['promises'] in ('PASS', 'FAIL', 'n/a')
          for row in exports['recipes']))

# --- retention ----------------------------------------------------------

root = fresh_logs('retention')


def persist(recipe_id, cook_id, pad=0, pages=1):
    for _page in range(pages):
        rec = auditmod.Recorder(fuzz.default_recipe(recipe_id),
                                cook_id=cook_id)
        rec.sidecar['pad'] = {'text': 'x' * pad}
        logger = logmod.FeedDecisionLogger(recipe_id, rec, cook_id)
        logger.persist()
    time.sleep(0.004)


def cooks_of(recipe_id):
    return {re.search(r'__c([0-9a-f]+)_', name).group(1)
            for name in os.listdir(root)
            if name.startswith('session_') and '_%s__c' % recipe_id in name}


for number in range(25):
    persist('busy', '%012x' % number, pages=2)
    if number < 5:
        persist('quiet', '%012x' % (100 + number))
busy, quiet = cooks_of('busy'), cooks_of('quiet')
check('a busy recipe keeps its last 20 cooks (%d), all their pages'
      % len(busy), busy == {'%012x' % n for n in range(5, 25)}
      and sum(1 for n in os.listdir(root) if '_busy__c' in n
              and not n.endswith('.text.json')) == 40)
check('a busy recipe never evicts another one (%d cooks of 5)' % len(quiet),
      len(quiet) == 5)
removed = logmod.clear_recipe('quiet')
check('clear_recipe removes one recipe only (%d cooks)' % removed,
      removed == 5 and not cooks_of('quiet') and cooks_of('busy'))
logmod.MAX_BYTES = 300 * 1024
for number in range(12):
    persist('big', '%012x' % (200 + number), pad=60 * 1024)
total = sum(os.path.getsize(os.path.join(root, n)) for n in os.listdir(root))
check('the folder stays under its size cap (%d of %d bytes), oldest first'
      % (total, logmod.MAX_BYTES), total <= logmod.MAX_BYTES
      and '%012x' % 211 in cooks_of('big') and '%012x' % 200 not in
      cooks_of('big'))
logmod.MAX_BYTES = 50 * 1024 * 1024
removed = logmod.clear_all()
check('clear_all removes every log and sidecar (%d cooks)' % removed,
      removed > 0 and not [n for n in os.listdir(root)
                           if n.startswith('session_')])

# --- the source: one cook id per refresh session, the audit of a post ------

from gi.repository import GLib  # noqa: E402

from src import feedchefbridge  # noqa: E402
from src.feedchef.engine import FeedResult  # noqa: E402
from src.feedchefsource import FeedChefSource, viewer_hides  # noqa: E402


class _Logger:
    def __init__(self, record):
        self.record = record


class _FakeEngine:
    def __init__(self):
        self.calls = []

    def cancel(self):
        pass

    def process_feed(self, recipe, **kwargs):
        self.calls.append(kwargs)
        number = len(self.calls)
        record = {'posts': [{'id': 'p%d' % number}]}
        kwargs['on_done'](FeedResult(
            items=[{'id': 'p%d' % number}], next_page_max_id=None,
            has_more=True, scope_oldest_id='c%d' % number,
            logger=_Logger(record)))


def fetch(source, params):
    got = []
    source.fetch(params, lambda _obj, result, _data: got.append(result))
    context = GLib.MainContext.default()
    for _spin in range(200):
        if got:
            break
        context.iteration(False)
    return source.finish(got[0])


engine = _FakeEngine()
source = FeedChefSource(engine, fuzz.default_recipe('source'))
fetch(source, {})
fetch(source, {'max_id': 'x'})
fetch(source, {})
first, older, refresh = engine.calls
check('an older page goes on with its cook, a refresh starts another',
      first['cook_id'] == older['cook_id'] != refresh['cook_id']
      and (first['page_index'], older['page_index'],
           refresh['page_index']) == (0, 1, 0)
      and first['hop'] == older['hop'] == 0)
found = source.audit_for('p3')
check('audit_for finds a post of the shown page, last_audit is its page',
      found is not None and found[0]['id'] == 'p3'
      and source.last_audit is found[1])
check('a refresh forgets the pages of the cook it replaced',
      source.audit_for('p1') is None and source.audit_for('zzz') is None)
check('the hide filter takes the one clock reading of the page',
      viewer_hides.takes_now is True)
check('the engine is made with the version the launcher sets, "dev" '
      'otherwise', feedchefbridge.app_version() == 'dev')

# --- cost: at most 5 ms (p95) per 40-post page --------------------------

fresh_logs('timing')
deltas = []
shown = []
for case in range(TIMED):
    rng = random.Random(5000 + case)
    recipe = fuzz.random_recipe(rng)
    world = fuzz.World(rng, recipe)
    cells = {}
    for flag in (False, True, False, True, False, True):
        logmod.clear_all()
        started = time.perf_counter()
        pages = fuzz.cook(world, recipe, case, pages=1, count=40,
                          audit=flag, now=fuzz.NOW, hide=hide_some)
        cells.setdefault(flag, []).append(time.perf_counter() - started)
    deltas.append((min(cells[True]) - min(cells[False])) * 1000.0)
    shown.append(len(pages[0].items))
deltas.sort()
p95 = deltas[min(len(deltas) - 1, int(len(deltas) * 0.95))]
print('# capture cost per page over %d pages: median %.2f ms, p95 %.2f ms, '
      'max %.2f ms' % (len(deltas), statistics.median(deltas), p95,
                       deltas[-1]))
check('capture adds at most 5 ms (p95) per 40-post page (%.2f ms)' % p95,
      p95 <= 5.0)

sys.exit(1 if failures else 0)
