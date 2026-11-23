# SPDX-License-Identifier: GPL-3.0-or-later
"""``collect``: replay and evaluate every log, one report per recipe.

``--json`` writes counts, verdicts, the recipe hash and dates only: no post
id, handle or text, and not even the recipe's name, so the file can leave
the machine.
"""

import datetime
import json
import os

from feedchefaudit import evaluate as evalmod
from feedchefaudit import replay as replaymod
from feedchefaudit.records import LogError, Loaded, expand, load


def _day(stamp):
    return datetime.date.fromtimestamp(stamp).isoformat()


def _new(record):
    snapshot = record['recipe']['snapshot']
    return {'name': snapshot['meta']['name'], 'cooks': set(), 'pages': 0,
            'days': [], 'pantry_in': 0, 'selected': 0, 'by_reason': {},
            'replay': {'identical': 0, 'mismatch': 0, 'skipped': 0,
                       'cannot': 0, 'partial_posts': 0, 'drift': 0,
                       'unverified_posts': 0},
            'notes': [], 'tally': evalmod.Tally()}


def gather(loaded_list, force=False, unreadable=None):
    """{recipe hash: report row} over the audit records in *loaded_list*;
    the logs whose record cannot be read are appended to *unreadable* as
    (loaded, why)."""
    rows = {}
    cooks = evalmod.group_cooks(loaded_list, unreadable)
    for (digest, cook), pages in cooks.items():
        for loaded in pages:
            record = loaded.record
            row = rows.setdefault(digest, _new(record))
            row['cooks'].add(cook)
            row['pages'] += 1
            row['days'].append(_day(loaded.timestamp))
            if record['outcome']['status'] == 'cancelled':
                row['cancelled'] = row.get('cancelled', 0) + 1
                continue
            row['pantry_in'] += record['summary']['pantry_in']
            row['selected'] += record['summary']['selected']
            for reason, count in record['summary']['by_reason'].items():
                row['by_reason'][reason] = \
                    row['by_reason'].get(reason, 0) + count
            _replay(row, loaded, force)
        try:
            rows[digest]['tally'].merge(evalmod.evaluate_cook(pages))
        except LogError as error:
            rows[digest]['notes'].append(str(error))
    return rows


def _replay(row, loaded, force):
    try:
        result = replaymod.replay(loaded, force)
    except LogError as error:
        row['replay']['cannot'] += 1
        row['notes'].append(str(error))
        return
    row['replay'][result.status] += 1
    row['replay']['partial_posts'] += result.partial
    row['replay']['unverified_posts'] += result.unverified
    row['replay']['drift'] += len(result.drift)
    if result.status == 'mismatch':
        row['notes'].append('replay mismatch: %s' % result.detail)


def _export(rows, unreadable=0):
    recipes = []
    for digest, row in sorted(rows.items()):
        tally = row['tally']
        recipes.append({
            'recipe_hash': digest, 'cooks': len(row['cooks']),
            'pages': row['pages'], 'first_day': min(row['days']),
            'last_day': max(row['days']), 'pantry_in': row['pantry_in'],
            'selected': row['selected'], 'by_reason': row['by_reason'],
            'replay': row['replay'],
            'verdicts': {rule: tally.verdict(rule)
                         for rule in evalmod.RULES},
            'violations': dict(tally.violations),
            'checked': dict(tally.checked),
            'unverifiable_removals': tally.unverifiable,
            'text_unverified': tally.text_unverified,
            'overall': tally.overall()})
    return {'schema': 1, 'recipes': recipes, 'unreadable_logs': unreadable}


def collect(paths, recipe_id=None, since=None, json_path=None, force=False,
            out=print, allow_partial=False):
    """Print the report; 0 when every replay is identical and every rule
    passes, 1 when not (a log that cannot be read counts), 2 when there is
    nothing to report on, 3 when some text could not be checked against
    its hash and *allow_partial* is not given."""
    loaded, unreadable = [], []
    for path in expand(paths):
        try:
            item = load(path)
        except LogError as error:
            unreadable.append((Loaded(path, None, None, None), str(error)))
            continue
        record = item.record
        if record is None:
            continue
        if isinstance(record, dict) and recipe_id and \
                (record.get('recipe') or {}).get('id') != recipe_id:
            continue
        if since and _day(item.timestamp) < since:
            continue
        loaded.append(item)
    rows = gather(loaded, force, unreadable)
    for item, why in unreadable:
        out('FAIL - %s: %s' % (os.path.basename(item.path), why))
    if not rows:
        out('no audit logs to report on')
        return 1 if unreadable else 2
    good = not unreadable
    unverified = False
    for digest, row in sorted(rows.items()):
        tally = row['tally']
        replay = row['replay']
        out('recipe %s  (hash %s)' % (row['name'], digest[:12]))
        out('  %d cooks, %d pages, %s to %s' % (
            len(row['cooks']), row['pages'], min(row['days']),
            max(row['days'])))
        out('  %d posts in the pantry, %d selected' % (
            row['pantry_in'], row['selected']))
        for reason, count in sorted(row['by_reason'].items(),
                                    key=lambda item: -item[1]):
            out('    %6d  %s' % (count, reason))
        out('  replay: %d identical, %d mismatch, %d skipped, %d cannot '
            '(%d posts replayed on recorded text verdicts, %d sentiment '
            'drift)' % (replay['identical'], replay['mismatch'],
                        replay['skipped'], replay['cannot'],
                        replay['partial_posts'], replay['drift']))
        for rule in evalmod.RULES:
            out('  %-16s %-4s  %d checked, %d violations' % (
                rule, tally.verdict(rule), tally.checked[rule],
                tally.violations[rule]))
            for example in tally.examples[rule]:
                out('      %s' % example)
        if row.get('cancelled'):
            out('  %d pages were cancelled and are not counted'
                % row['cancelled'])
        out('  overall: %s' % tally.overall())
        for note in row['notes'][:5]:
            out('  note: %s' % note)
        good = good and tally.ok() and not replay['mismatch'] \
            and not replay['cannot']
        unverified = unverified or bool(tally.text_unverified
                                        or replay['unverified_posts'])
    if json_path:
        with open(json_path, 'w', encoding='utf-8') as handle:
            json.dump(_export(rows, len(unreadable)), handle, indent=2,
                      sort_keys=True)
            handle.write('\n')
    if not good:
        return 1
    return 3 if unverified and not allow_partial else 0
