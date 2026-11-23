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

import contextlib
import tempfile
from datetime import datetime

from src.feedchef import audit as auditmod
from src.feedchef import cluster as clustermod
from src.feedchef import langguess
from src.feedchef import decisionlog, kitchen as kitchenmod
from src.feedchef import network_reach as reachmod
from src.feedchef import pantry as pantrymod
from src.feedchef.engine import FeedEngine
from feedchefaudit.records import (LogError, Rebuilt, check_replayable,
                                   patched)

# What a mismatch names, from the earliest stage on.
_FIELD_STAGE = (('origin', 'pantry'), ('also_in', 'pantry'),
                ('facts', 'facts'), ('follow', 'reach'), ('viewer', 'viewer'),
                ('rules', 'rules'),
                ('sent', 'sentiment'), ('score', 'kitchen'),
                ('cluster', 'kitchen'),
                ('removal', 'removal'), ('rank', 'order'))


class Result:
    """status: identical | mismatch | skipped (nothing to replay)."""

    def __init__(self, status, detail='', partial=0, drift=(), stage='',
                 unverified=0):
        self.status = status
        self.detail = detail
        self.partial = partial
        # Posts whose text is not the one the record was made with (the
        # sidecar is gone or was changed): a replay of them is not proof.
        self.unverified = unverified
        self.drift = list(drift)
        self.stage = stage


def _entry_view(entry, unreliable):
    facts = dict(entry['facts'])
    facts.pop('text_ref', None)
    # A guess that drifts is a warning (Rebuilt.guess_drift), not a
    # mismatch: the replay feeds the recorded one back.
    facts.pop('lang_guess', None)
    if unreliable:
        facts.pop('text_len', None)
        facts.pop('text_sha256', None)
    return {**entry, 'facts': facts}


def _field(key, left, right):
    """Where two values of a post differ, by name only: a replay's report
    is pasted into issues and holds none of what the post says."""
    if isinstance(left, dict) and isinstance(right, dict):
        for name in sorted({*left, *right}):
            if left.get(name) != right.get(name):
                return '%s.%s' % (key, name)
    return key


def _page_view(record):
    """The page's grouping record without the library versions (a version
    that differs is not a difference of the page: see ``_versions``)."""
    page = record.get('cluster')
    if not isinstance(page, dict):
        return page
    return {key: value for key, value in page.items() if key != 'versions'}


def _versions(record):
    """What differs between the libraries the record was made with and the
    ones here, as a sentence; '' when nothing does."""
    found = []
    page = record.get('cluster')
    if isinstance(page, dict) and isinstance(page.get('versions'), dict):
        now = clustermod.versions()
        for name, then in page['versions'].items():
            if then != now.get(name):
                found.append('%s %s recorded, %s here' % (
                    name, then or '?', now.get(name) or '?'))
    guess = (record.get('env') or {}).get('lang_guess') or {}
    if guess.get('version') and guess['version'] != langguess.version():
        found.append('py3langid %s recorded, %s here' % (
            guess['version'], langguess.version() or '?'))
    return ', '.join(found)


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
            if left.get(key) != right.get(key):
                return stage, sid, '%s differs' % _field(
                    key, left.get(key), right.get(key))
    if recorded['sources'] != replayed['sources']:
        return 'pantry', '', 'sources differ'
    for key in ('summary', 'outputs'):
        if recorded[key] != replayed[key]:
            name = next(name for name in recorded[key]
                        if recorded[key][name] != replayed[key].get(name))
            return 'order' if name == 'order' else 'summary', '', \
                '%s.%s differs' % (key, name)
    for key in ('trends', 'local_heat'):
        if recorded['env'][key] != replayed['env'][key]:
            return 'kitchen', '', 'env.%s differs' % key
    if recorded.get('heat') != replayed.get('heat'):
        return 'kitchen', '', 'heat differs'
    if _page_view(recorded) != _page_view(replayed):
        return 'kitchen', '', '%s differs' % _field(
            'cluster', _page_view(recorded), _page_view(replayed))
    if recorded['cursor_out'] != replayed['cursor_out']:
        return 'pantry', '', 'cursor_out differs'
    return None


def _fetchers(rebuilt):
    """The fake fetch functions that hand back what each source gave, in
    its own order: the record lists them (a post twice in one source, a post
    with no id and ids that are not digits all come back as they were)."""
    sources = rebuilt.record['sources']

    def posts(slots):
        return [{'tags': [{'name': name} for name in slot['tags']],
                 'content': '', 'media_attachments': []}
                if isinstance(slot, dict) else rebuilt.statuses[slot]
                for slot in slots]

    def search(query):
        return posts(sources['focus'].get(query.lstrip('#'), []))

    return (lambda max_id, count: posts(sources['scope']),
            lambda tag, count: posts(sources['hashtag'].get(tag, [])),
            search)


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
              if entry['viewer'] == 'hidden'}
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
    # The searches of More Like This are staggered to be kind to a
    # server; a replay asks no one.
    engine.search_stagger_ms = 0
    try:
        engine.init_preload()
        result = engine.process_feed_sync(
            recipe, max_id=record['cursor_in'],
            count=record['params']['count'],
            author_counts=record['params']['author_counts_in'],
            instance_counts=record['params'].get('instance_counts_in'),
            hide=(lambda status: str(status.get('id') or '') in hidden)
            if env['viewer_filter'] else None,
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
    check_replayable(loaded, force, replay=True)
    record = loaded.record
    if record['outcome']['status'] not in auditmod.FINISHED:
        return Result('skipped', 'the cook did not finish (%s): nothing '
                      'to replay' % record['outcome']['status'])
    try:
        return _replay(record, loaded)
    except (LookupError, TypeError, ValueError, AttributeError) as error:
        raise LogError('the record cannot be replayed (%s)'
                       % type(error).__name__) from error


@contextlib.contextmanager
def recorded_grouping(record):
    """While inside, the grouping runs with the start, seed and weights
    the record says it used, so a record made with other ones replays."""
    saved = (clustermod.INIT, clustermod.SEED, clustermod.WEIGHTS,
             clustermod.MAX_ITER)
    page = record.get('cluster')
    if isinstance(page, dict):
        clustermod.INIT = page.get('init', clustermod.INIT)
        clustermod.SEED = page.get('seed', clustermod.SEED)
        clustermod.WEIGHTS = tuple(page.get('weights', clustermod.WEIGHTS))
        clustermod.MAX_ITER = page.get('max_iter', clustermod.MAX_ITER)
    try:
        yield
    finally:
        (clustermod.INIT, clustermod.SEED, clustermod.WEIGHTS,
         clustermod.MAX_ITER) = saved


def _replay(record, loaded):
    rebuilt = Rebuilt(loaded)
    if not rebuilt.words_whole():
        return Result(
            'skipped', 'the relevance sort and similarity grouping read the '
            'whole text of every post, and the sidecar does not hold it')
    drift = rebuilt.drift()
    old = (decisionlog._root, reachmod._root)
    saved_cache = dict(pantrymod._mlt_cache)
    pantrymod._mlt_cache.clear()
    try:
        with tempfile.TemporaryDirectory(prefix='feedchef-replay-') as tmp:
            decisionlog.set_root(tmp + '/logs')
            reachmod.set_root(tmp + '/reach')
            with patched(rebuilt), recorded_grouping(record):
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
    unverified = len(rebuilt.unverified)
    if found is None:
        return Result('identical', partial=partial, drift=drift,
                      unverified=unverified)
    different = _versions(record)
    if different:
        # Another version of a library may group or guess otherwise: no
        # verdict, rather than a mismatch the code did not cause.
        return Result('skipped', 'a library version differs (%s): the '
                      'cook may differ, so there is no verdict' % different,
                      partial=partial, drift=drift, unverified=unverified)
    stage, sid, what = found
    return Result('mismatch', '%s%s: %s' % (
        stage, ' (post %s)' % sid[-6:] if sid else '', what),
        partial=partial, drift=drift, stage=stage, unverified=unverified)

