# SPDX-License-Identifier: GPL-3.0-or-later
"""``quality``: how each recipe's cooks did, from the audit records.

The same numbers the app's Kitchen Report shows (``src/feedchef/quality.py``,
``health.py``, ``advice.py``), computed offline from the logs: per recipe, the
metrics of the pantry, diet and kitchen stages of its latest cook, the score
of each cook, the recipe's health and the recommendations. Nothing is
fetched, and nothing here judges the audit's rules (``evaluate`` does).

**Read the score for what it is.** Babble enforces every filter, so validity
is 100% at the diet and kitchen stages and the recipe score mostly measures
engagement. The report says so, and so does its JSON (``caveat``).

``--json`` writes numbers, ids, verdicts, the recipe's hash and dates only: no
post id, text or handle, no recipe name, and a hashtag list only as its length.
"""

import datetime
import json

from feedchefaudit import evaluate as evalmod
from feedchefaudit.records import LogError, expand, load, recipe_of
from src.feedchef import advice, health, quality

SCHEMA = 1


def _day(stamp):
    return datetime.date.fromtimestamp(stamp).isoformat()


def _plain(value):
    """A value of a recommendation for the export: a list (hashtags: the
    recipe's own words) only as its length."""
    return len(value) if isinstance(value, list) else value


def _stage_row(stage):
    keep = ('total_toots', 'valid_toots', 'validity_rate',
            'avg_quality_score', 'median_quality_score',
            'avg_relevance_score', 'rejection_reasons',
            'quality_distribution', 'quality_percentiles',
            'relevance_percentiles')
    return {key: stage[key] for key in keep if key in stage}


def analyse(cooks, others=()):
    """The quality of one recipe from its cooks: *cooks* is a list of lists
    of loaded logs (the pages of each cook), in any order. None when no cook
    has a shown page. *others* are the reader's other recipes, for the
    comparison."""
    ordered = sorted(cooks, key=lambda pages: min(p.timestamp
                                                  for p in pages))
    groups = [[page.record for page in pages] for pages in ordered]
    summaries = [found for found in map(quality.cook_summary, groups)
                 if found['pages']]
    if not summaries:
        return None
    latest = next(group for group in reversed(groups)
                  if quality.cook_summary(group)['pages'])
    metrics = quality.assess_pages(latest)
    state = health.assess(summaries)
    snapshot = recipe_of(latest[-1])
    found = advice.recommend(snapshot, metrics, list(others))
    return {'metrics': metrics, 'summaries': summaries, 'health': state,
            'advice': found, 'days': [
                _day(min(p.timestamp for p in pages)) for pages in ordered]}


def export(report):
    """The numbers of an analysis, for a file that may leave the machine."""
    state = report['health']
    metrics = report['metrics']
    return {
        'caveat': quality.CAVEAT,
        'recipe_quality_score': metrics['recipe_quality_score'],
        'state': metrics['state'],
        'stages': {name: _stage_row(metrics[name])
                   for name in quality.STAGES},
        'cooks': [{'score': item['score'], 'posts': item['posts'],
                   'state': item['state'], 'zero_scores':
                   item['zero_scores'], 'fresh_fallback':
                   item['fresh_fallback'], 'heat_stepped':
                   item['heat_stepped']} for item in report['summaries']],
        'health': {'state': state.state, 'reasons': list(state.reasons),
                   'mean': state.mean, 'cooks': state.cooks,
                   'alert': state.alert},
        'advice': [{**rec.to_json(), 'current': _plain(rec.current),
                    'suggested': _plain(rec.suggested)}
                   for rec in report['advice']],
    }


def lines(report, out=print, prefix='  '):
    """The report as text lines (the caveat first)."""
    metrics = report['metrics']
    state = report['health']
    out('%squality: %s' % (prefix, quality.caveat_text()))
    out('%s  latest cook: recipe score %.3f (%s), %d posts shown' % (
        prefix, metrics['recipe_quality_score'], metrics['state'],
        metrics['kitchen']['total_toots']))
    for name in quality.STAGES:
        stage = metrics[name]
        bands = stage.get('quality_distribution') or {}
        out('%s  %-8s %4d posts  validity %5.1f%%  engagement avg %.1f '
            'median %.1f  bands %s' % (
                prefix, name, stage['total_toots'], stage['validity_rate'],
                stage['avg_quality_score'],
                stage.get('median_quality_score', 0.0),
                ' '.join('%s %d' % (band, bands.get(band, 0))
                         for band in quality.BANDS)))
    out('%s  health: %s%s over %d cooks%s' % (
        prefix, state.state,
        ' (%s)' % ', '.join(state.reasons) if state.reasons else '',
        state.cooks, ', ALERT: under %.1f for %d cooks' % (
            health.ALERT_BELOW, health.WINDOW) if state.alert else ''))
    if state.mean is not None:
        out('%s  cook scores: %s' % (prefix, ' '.join(
            '%.2f' % item['score'] for item in report['summaries'][-10:])))
    for rec in report['advice']:
        words = advice.describe(rec)
        out('%s  advice [%s] %s: %s -- %s%s' % (
            prefix, rec.severity, words.title,
            words.change or 'needs your choice', words.reason,
            ' (%s)' % words.impact if words.impact else ''))


def _variant(pages):
    """The A/B variant a cook was (its first page's record), or None."""
    found = pages[0].record.get('variant')
    return found if isinstance(found, dict) else None


def experiments(loaded_list):
    """The A/B experiments in the logs, one row each: how many cooks of
    each arm, and their mean recipe score. Numbers only: no seed, no
    recipe id, no hash."""
    found = {}
    for pages in evalmod.group_cooks(loaded_list).values():
        variant = _variant(pages)
        if variant is None or variant.get('id') not in ('A', 'B'):
            continue
        key = (pages[0].record['recipe']['id'], variant.get('seed'))
        scores = [s for s in (quality.cook_summary(
            [p.record for p in pages]),) if s['pages']]
        if scores:
            found.setdefault(key, {'A': [], 'B': []})[
                variant['id']].append(scores[0]['score'])
    rows = []
    for arms in found.values():
        rows.append({
            'cooks_a': len(arms['A']), 'cooks_b': len(arms['B']),
            'mean_a': sum(arms['A']) / len(arms['A']) if arms['A'] else None,
            'mean_b': sum(arms['B']) / len(arms['B']) if arms['B'] else None})
    return rows


def experiment_line(row):
    def part(count, mean):
        return '%d cooks (mean %s)' % (count, '%.2f' % mean
                                       if mean is not None else '-')
    return 'experiment (A/B): A %s, B %s' % (
        part(row['cooks_a'], row['mean_a']),
        part(row['cooks_b'], row['mean_b']))


def gather(loaded_list):
    """{recipe hash: analysis} over the loaded logs (the cooks the audit's
    own grouping finds, a page cooked twice once). The cooks of an A/B
    experiment's variant B are the proposed recipe's, not the recipe's:
    they are reported by ``experiments`` and not as a recipe of their
    own."""
    by_hash = {}
    for (digest, _cook), pages in evalmod.group_cooks(loaded_list).items():
        variant = _variant(pages)
        if variant is not None and variant.get('id') == 'B':
            continue
        by_hash.setdefault(digest, []).append(pages)
    found = {}
    for digest, cooks in by_hash.items():
        try:
            report = analyse(cooks)
        except LogError:
            continue
        if report is not None:
            found[digest] = report
    return found


def run(paths, recipe_id=None, since=None, json_path=None, out=print):
    """Print the quality of each recipe in the logs; 0, or 2 when there is
    nothing to report on."""
    loaded = []
    for path in expand(paths):
        try:
            item = load(path)
        except LogError:
            continue
        record = item.record
        if not isinstance(record, dict):
            continue
        if recipe_id and (record.get('recipe') or {}).get('id') != recipe_id:
            continue
        if since and _day(item.timestamp) < since:
            continue
        loaded.append(item)
    found = gather(loaded)
    if not found:
        out('no audit logs to report quality on')
        return 2
    for digest, report in sorted(found.items()):
        out('recipe (hash %s)  %d cooks, %s to %s' % (
            digest[:12], len(report['summaries']), min(report['days']),
            max(report['days'])))
        lines(report, out)
    tests = experiments(loaded)
    for row in tests:
        out(experiment_line(row))
    if json_path:
        with open(json_path, 'w', encoding='utf-8') as handle:
            json.dump({'schema': SCHEMA, 'experiments': tests,
                       'recipes': [
                {'recipe_hash': digest, 'first_day': min(rep['days']),
                 'last_day': max(rep['days']), **export(rep)}
                for digest, rep in sorted(found.items())]},
                handle, indent=2, sort_keys=True)
            handle.write('\n')
    return 0
