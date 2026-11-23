# SPDX-License-Identifier: GPL-3.0-or-later
"""Replay of one audit record through the real FeedEngine.

The record holds every input of the page: the posts and where each came
from, the clock, the trends, the follow verdicts, the viewer's hides (the
user's filters are not replayable, their verdicts are recorded) and the
sentiment each post was judged with. The engine is built over fake fetches
that hand those back, and the record it then writes is compared with the
one it replays: same posts, same reasons, same order, same scores, bit for
bit. Nothing about the comparison is loosened to make a record pass.
"""

import tempfile
from datetime import datetime

from src.feedchef import decisionlog, kitchen as kitchenmod
from src.feedchef import network_reach as reachmod
from src.feedchef import pantry as pantrymod
from src.feedchef.engine import FeedEngine
from feedchefaudit.records import (LogError, Rebuilt, check_replayable,
                                   patched)

# What a mismatch names, from the earliest stage on.
_FIELD_STAGE = (('origin', 'pantry'), ('also_in', 'pantry'),
                ('facts', 'facts'), ('follow', 'reach'), ('rules', 'rules'),
                ('sent', 'sentiment'), ('score', 'kitchen'),
                ('removal', 'removal'), ('rank', 'order'))


class Result:
    """status: identical | mismatch | skipped (nothing to replay)."""

    def __init__(self, status, detail='', partial=0, drift=(), stage=''):
        self.status = status
        self.detail = detail
        self.partial = partial
        self.drift = list(drift)
        self.stage = stage


def _entry_view(entry, unreliable):
    facts = dict(entry['facts'])
    facts.pop('text_ref', None)
    if unreliable:
        facts.pop('text_len', None)
    return {**entry, 'facts': facts}


def first_difference(recorded, replayed, unreliable=()):
    """(stage, post id, what) of the first difference between two records,
    or None when they agree on everything a replay reproduces."""
    mine = {entry['id']: entry for entry in recorded['posts']}
    other = {entry['id']: entry for entry in replayed['posts']}
    for sid in mine:
        if sid not in other:
            return 'pantry', sid, 'recorded, not in the replay'
    for sid in other:
        if sid not in mine:
            return 'pantry', sid, 'in the replay, not recorded'
    for sid, entry in mine.items():
        left = _entry_view(entry, sid in unreliable)
        right = _entry_view(other[sid], sid in unreliable)
        for key, stage in _FIELD_STAGE:
            if left[key] != right[key]:
                return stage, sid, '%s: recorded %r, replay %r' % (
                    key, left[key], right[key])
    for key in ('summary', 'outputs'):
        if recorded[key] != replayed[key]:
            name = next(name for name in recorded[key]
                        if recorded[key][name] != replayed[key].get(name))
            return 'order' if name == 'order' else 'summary', '', \
                '%s.%s differs' % (key, name)
    for key in ('trends', 'local_heat'):
        if recorded['env'][key] != replayed['env'][key]:
            return 'kitchen', '', 'env.%s differs' % key
    if recorded['cursor_out'] != replayed['cursor_out']:
        return 'pantry', '', 'cursor_out differs'
    return None


def _fetchers(rebuilt):
    """The fake fetch functions that hand back the recorded posts, each
    in the sources the record says it came from."""
    scope, tags, focus = [], {}, {}
    for entry in rebuilt.entries():
        status = rebuilt.statuses[entry['id']]
        for label in [entry['origin'], *entry['also_in']]:
            if label == 'scope':
                scope.append(status)
            elif label.startswith('hashtag:'):
                tags.setdefault(label[len('hashtag:'):], []).append(status)
            else:
                focus.setdefault(label[len('focus:'):], []).append(status)

    def search(query):
        return list(focus.get(query.lstrip('#'), []))

    return (lambda max_id, count: list(scope),
            lambda tag, count: list(tags.get(tag, [])), search)


def run(rebuilt):
    """Cook the recorded page again; the record it writes."""
    record = rebuilt.record
    recipe = rebuilt.recipe
    env = record['env']
    scope, hashtag, search = _fetchers(rebuilt)
    follows = sorted(rebuilt.follow_ids())
    trends = kitchenmod.ServerTrendContext(
        trending_hashtags=dict(env['trends']['tags']),
        trending_status_ids=set(env['trends']['status_ids']))
    hidden = {entry['id'] for entry in rebuilt.entries()
              if entry['removal'] and entry['removal']['reason']
              == 'viewer_hide'}
    now = datetime.fromisoformat(record['clock']['now_utc'])
    engine = FeedEngine(
        account_key='replay#' + str(record['account']['id']),
        self_account_id=env['self_id'], fetch_scope=scope,
        fetch_hashtag=hashtag, search_statuses=search,
        fetch_trends=lambda: trends,
        fetch_following_page=lambda max_id: (
            [{'id': person} for person in follows], None),
        native_bluesky=lambda: env['native_bluesky'],
        app_version=record['app']['version'])
    engine.clock = lambda: now
    try:
        engine.init_preload()
        result = engine.process_feed_sync(
            recipe, max_id=record['cursor_in'],
            count=record['params']['count'],
            author_counts=record['params']['author_counts_in'],
            hide=lambda status: str(status.get('id') or '') in hidden,
            cook_id=record['cook_id'], page_index=record['page_index'],
            hop=record['hop'])
    finally:
        engine.shutdown()
        # The follow-list preload may still be writing its cache file.
        engine._reach_executor.shutdown(wait=True)
    return result.logger.record


def replay(loaded, force=False):
    """A Result for one loaded log. Raises LogError for a record that
    cannot be replayed."""
    check_replayable(loaded, force)
    record = loaded.record
    if record['outcome']['status'] != 'ok':
        return Result('skipped', 'the cook failed: nothing to replay')
    rebuilt = Rebuilt(loaded)
    drift = rebuilt.drift()
    old = (decisionlog._root, reachmod._root)
    saved_cache = dict(pantrymod._mlt_cache)
    pantrymod._mlt_cache.clear()
    try:
        with tempfile.TemporaryDirectory(prefix='feedchef-replay-') as tmp:
            decisionlog.set_root(tmp + '/logs')
            reachmod.set_root(tmp + '/reach')
            with patched(rebuilt):
                again = run(rebuilt)
    finally:
        decisionlog.set_root(old[0])
        reachmod.set_root(old[1])
        pantrymod._mlt_cache.clear()
        pantrymod._mlt_cache.update(saved_cache)
    if again is None:
        raise LogError('the replay wrote no audit record')
    found = first_difference(record, again, rebuilt.unreliable)
    partial = len(rebuilt.unreliable)
    if found is None:
        return Result('identical', partial=partial, drift=drift)
    stage, sid, what = found
    return Result('mismatch', '%s%s: %s' % (
        stage, ' (post %s)' % sid[-6:] if sid else '', what),
        partial=partial, drift=drift, stage=stage)

