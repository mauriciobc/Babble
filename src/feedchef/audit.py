# SPDX-License-Identifier: GPL-3.0-or-later
"""The audit record of a Feed Chef cook (no widgets, no GTK).

The engine only *observes* through this module: every hook takes a recorder,
the default ``NullAudit`` does nothing, and no selection code ever reads from
it. Facts are captured where they are already known (origin in
``pantry.merge_pantry``, rule results in ``contract.violations``, score factors in the
kitchen modifiers), and each removed post has exactly one reason, from the
closed set below. A record holds every input a cook needs, so a replay
(``bench/feedchef_audit.py replay``) can reproduce the selection, the order and
the scores exactly.

Schema 2, stored under the ``audit`` key of a decision log, next to the v1
``stages``. The visible text of each post is *not* in it: it goes to a private
sidecar (``<log>.text.json``, mode 0600) that is never exported.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from . import auditbuild, contract
# The record's constants and small functions live in a leaf module both
# this one and ``auditbuild`` import; they are re-exported for callers.
from .auditconst import (ENGINE_REV, SCHEMA, TEXT_CAP,  # noqa: F401
                         recipe_hash, stage_of, text_digest)
from .recipe import FeedRecipe
from .textutil import content_of

log = logging.getLogger('babble.feedchef')

# The outcomes of a page that finished: 'ok' (shown as a page of the cook),
# 'peek' (cooked by Smart New Posts, not shown) and 'shown' (a peek whose
# new posts were inserted at the top). Anything else (failed, cancelled)
# has nothing to judge.
FINISHED = ('ok', 'peek', 'shown')
# What counts as a page of the cook in the Kitchen Report and the caps.
SHOWN = ('ok', 'shown')

PANTRY_REASONS = ('pantry_excluded_tag', 'pantry_missing_included_tag')
# Revision 3 added cluster_cap and instance_cap. Revision 5 added the page's
# freshness fallback (``fresh_fallback``) and the optimizer's ``variant``.
FIXED_REASONS = frozenset(PANTRY_REASONS + (
    'reach_not_followed', 'viewer_hide', 'heat_cut', 'author_cap',
    'cluster_cap', 'instance_cap'))
_RANK = {'focus': 0, 'hashtag': 1, 'scope': 2}


def is_reason(reason: str) -> bool:
    """Whether *reason* is in the closed set of removal reasons."""
    if reason in FIXED_REASONS:
        return True
    kind, _sep, promise = reason.partition(':')
    if kind == 'diet':
        return promise in contract.DIET_IDS
    return kind == 'guard' and (promise in contract.PROMISE_IDS
                                or promise in contract.LIST_IDS)


_URL_QUERY = re.compile(r'(https?://[^\s?#]*)[?#]\S*')
_SECRET = re.compile(
    r'(?i)\b(access_token|refresh_token|token|client_secret|password|'
    r'authorization)\b\s*[=:]\s*[^\s&;,]+')
_BEARER = re.compile(r'(?i)\bbearer\s+\S+')


def redact(text: str, limit: int = 300) -> str:
    """*text* without what must not reach a log that may be shared: a
    URL's query string and fragment, and the value of a token or secret."""
    text = _URL_QUERY.sub(r'\1', str(text or ''))
    text = _BEARER.sub('bearer …', text)
    return _SECRET.sub(lambda m: m.group(1) + '=…', text)[:limit]


def _rank_of(label: str) -> int:
    return _RANK[label.partition(':')[0]]


def sidecar_path(log_path: str) -> str:
    """The text sidecar of a log: ``session_X.json`` -> ``session_X.text.json``."""
    return log_path[:-len('.json')] + '.text.json'


def write_private(path: str, payload: Any) -> None:
    """Write *payload* as JSON readable by this user only (0600), through
    a temporary file so a reader never sees half of it."""
    tmp = path + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        os.fchmod(handle.fileno(), 0o600)
        # dumps, not dump: the one-shot encoder is the C one, several times
        # faster than the chunked one `dump` uses (the bytes are the same).
        handle.write(json.dumps(payload, ensure_ascii=False,
                                separators=(',', ':')))
        handle.write('\n')
    os.replace(tmp, path)


class PantryTrace:
    """Where each post came from and why the pantry dropped it, filled by
    ``pantry.merge_pantry`` (an out-map: it is never read back there).

    The engine fills ``tag_of`` (per-tag fetch results) and ``focus_of``
    (More Like This), because only it knows which hashtag a post came
    from; status dicts are never mutated."""

    def __init__(self):
        self.tag_of: dict[str, list[str]] = {}
        self.focus_of: dict[str, list[str]] = {}
        self.labels: dict[str, list[str]] = {}
        self.winner: dict[str, str] = {}
        self.removed: dict[str, str] = {}
        self.duplicates = 0
        self.no_id = 0
        # Per More Like This term, the posts its search gave, in order.
        self.focus_seq: dict[str, list[dict]] = {}

    def _labels(self, bucket: str, sid: str) -> list[str]:
        if bucket == 'scope':
            return ['scope']
        found = (self.focus_of if bucket == 'focus' else self.tag_of
                 ).get(sid) or ['?']
        prefix = 'focus:' if bucket == 'focus' else 'hashtag:'
        return [prefix + name for name in found]

    def appear(self, bucket: str, sid: str) -> None:
        seen = self.labels.setdefault(sid, [])
        for label in self._labels(bucket, sid):
            if label not in seen:
                seen.append(label)

    def drop(self, bucket: str, sid: str, reason: str) -> None:
        self.appear(bucket, sid)
        self.removed.setdefault(sid, reason)

    def keep(self, bucket: str, sid: str) -> None:
        self.appear(bucket, sid)
        self.winner.setdefault(sid, self._labels(bucket, sid)[0])

    def duplicate(self, bucket: str, sid: str) -> None:
        self.appear(bucket, sid)
        self.duplicates += 1

    def sources(self, sid: str) -> tuple[str, list[str]]:
        """(origin, also_in): the label that got the post into the pantry
        (or, for a dropped one, the first it appeared in), then the
        others in a fixed order."""
        labels = sorted(self.labels.get(sid, ['scope']), key=_rank_of)
        origin = self.winner.get(sid) or labels[0]
        return origin, [label for label in labels if label != origin]


class NullAudit:
    """The recorder of a cook nobody audits: every hook does nothing."""

    enabled = False
    trace = None

    def __getattr__(self, name):
        return _nothing


def _nothing(*_args, **_kwargs):
    return None


NULL = NullAudit()


class Recorder:
    """Collects one page's audit as the stages run; ``build()`` makes the
    record. All of it runs on the engine worker."""

    enabled = True

    def __init__(self, recipe: FeedRecipe, *, account_key: str = '',
                 self_id: str = '', cook_id: str = '', page_index: int = 0,
                 hop: int = 0, cursor_in: str | None = None, count: int = 0,
                 app_version: str = 'dev', session_id: str = '',
                 variant: dict | None = None):
        self.recipe = recipe
        # The cook of the A/B optimizer (feedchef.optimizer): which variant
        # this page cooked, with the seed that chose it. Observed only.
        self.variant = dict(variant) if variant else None
        self.fallback_fresh = False
        self.identity = {
            'session_id': session_id, 'cook_id': cook_id,
            'page_index': page_index, 'hop': hop, 'cursor_in': cursor_in,
            'cursor_out': None}
        self.account_key = account_key
        self.self_id = str(self_id or '')
        self.app_version = app_version or 'dev'
        self.count = count
        self.trace = PantryTrace()
        self.now: datetime | None = None
        self.native = False
        self.raw: list[dict] = []
        self.merged: list[dict] = []
        self.diet: dict[str, str] = {}
        self.follow_info = {'status': 'none', 'count': 0, 'fingerprint': ''}
        self.follow_ids: set[str] | None = None
        self.reach_dropped: set[str] = set()
        self.hidden: set[str] = set()
        self.viewer: dict[str, str] | None = None
        self.sources: dict = {'scope': [], 'hashtag': {}, 'focus': {}}
        self.cancelled_flag = False
        self.peek = False
        self.broken = ''
        self.trends: dict[str, Any] = {'tags': {}, 'status_ids': []}
        self.tag_heat: dict[str, float] = {}
        self.factors: dict[str, dict[str, float]] = {}
        self.kitchen_removed: dict[str, str] = {}
        self.guard_broken: dict[str, list[str]] = {}
        self.judged: dict[str, Any] = {}
        self.counts_in: dict[str, int] = {}
        self.counts_out: dict[str, int] = {}
        self.instances_in: dict[str, int] = {}
        self.instances_out: dict[str, int] = {}
        # Revision 3: the freshness floor, the relevance to the focus and
        # the similarity grouping of the page (None: it did not run).
        self.floored: set[str] = set()
        self.relevance_of: dict[str, float] | None = None
        self.grouping = None
        self.word_cache = None
        self.unmatched = False
        # The heat cuts (one row per heat modifier: the step asked for and
        # the one used) and the Hot badge of the page.
        self.heat_rows: list[dict] = []
        self.fire_ids: set[str] | None = None
        self.ordered: list[str] | None = None
        self.score_map: dict[str, float] = {}
        self.error = ''
        self.sidecar: dict[str, dict] = {}

    # -- hooks, in the order the engine calls them ------------------------

    def clock(self, now: datetime, native: bool = False) -> None:
        self.now = now
        self.native = native

    @staticmethod
    def _slot(status):
        """A post in a source's list: its id, or for a post with no id
        the tags it carries (a replay needs them to count it the same)."""
        sid = str(status.get('id') or '')
        if sid:
            return sid
        return {'tags': [str((tag or {}).get('name') or '')
                         for tag in content_of(status).get('tags') or []]}

    def fetched(self, mlt, tags, scope) -> None:
        self.raw = [*mlt, *tags, *scope]
        self.sources['scope'] = [self._slot(s) for s in scope]
        self.sources['focus'] = {
            term: [self._slot(s) for s in found]
            for term, found in self.trace.focus_seq.items()}

    def hashtag_fetched(self, tag: str, posts) -> None:
        self.sources['hashtag'][tag] = [self._slot(s) for s in posts]
        for status in posts:
            self.trace.tag_of.setdefault(
                str(status.get('id') or ''), []).append(tag)

    def pantry(self, items) -> None:
        self.merged = list(items)

    def counts(self, counts, instances=None) -> None:
        self.counts_in = dict(counts or {})
        self.instances_in = dict(instances or {})

    def follow_state(self, snapshot, *, error: bool = False,
                     needed: bool = True) -> None:
        if not needed:
            self.follow_info = {'status': 'none', 'count': 0,
                                'fingerprint': ''}
            return
        ids = None if snapshot is None else snapshot.following_ids
        if error or ids is None:
            self.follow_info = {'status': 'error', 'count': 0,
                                'fingerprint': ''}
            return
        self.follow_ids = set(ids)
        digest = hashlib.sha256(
            '\n'.join(sorted(ids)).encode('utf-8')).hexdigest()
        self.follow_info = {
            'status': 'stale' if snapshot.stale else 'fresh',
            'count': len(ids), 'fingerprint': digest[:16]}

    def diet_decisions(self, decisions) -> None:
        self.diet = {d.post_id: d.reason for d in decisions
                     if d.verdict != 'PASSED'}

    def reach_removed(self, before, after) -> None:
        kept = {id(item) for item in after}
        self.reach_dropped = {str(item.get('id') or '') for item in before
                              if id(item) not in kept}

    def viewer_checked(self, hidden, shown) -> None:
        """The viewer's own filter was asked about these posts."""
        self.hidden = set(hidden)
        self.viewer = {**dict.fromkeys(shown, 'shown'),
                       **dict.fromkeys(hidden, 'hidden')}

    def trends_seen(self, server) -> None:
        wanted = {str(content_of(s).get('id') or s.get('id') or '')
                  for s in self.merged}
        self.trends = {
            'tags': dict(server.trending_hashtags),
            'status_ids': sorted(set(server.trending_status_ids) & wanted)}

    def judged_scores(self, judge, statuses) -> None:
        for status in statuses:
            found = judge.peek(status)
            if found is not judge.MISSING:
                self.judged[str(status.get('id') or '')] = found

    def heat_map(self, tag_heat) -> None:
        self.tag_heat = dict(tag_heat)

    def factor(self, sid: str, kind: str, value: float) -> None:
        row = self.factors.setdefault(sid, {})
        row[kind] = row[kind] * value if kind in row and kind != 'base' \
            else value

    def fresh_fallback(self) -> None:
        """Every score of the page was zero because freshness faded every
        post, so the page used the floor of 0.2 (``kitchen.score_statuses``)."""
        self.fallback_fresh = True

    def fresh_floor(self, sid: str) -> None:
        """The freshness factor of this post was raised to the floor."""
        self.floored.add(sid)

    def relevance(self, scores, keywords, weights) -> None:
        """The relevance of each post to the focus (the cosine of its
        TF-IDF vector with the reference vector of the recipe's keywords
        and weights, which the recipe snapshot holds)."""
        self.relevance_of = dict(scores)

    def focus_unmatched(self) -> None:
        """No post of the page had any relevance to the focus: the page was
        ordered as the recipe says, not by relevance."""
        self.unmatched = True

    def words(self, cache) -> None:
        """The cook's cache of the words of the posts, which ``_facts``
        reads the focus text from instead of parsing every post again."""
        self.word_cache = cache

    def cluster(self, outcome) -> None:
        """The similarity grouping of the page (a ``cluster.ClusterRun``)."""
        self.grouping = outcome

    def heat_steps(self, rows) -> None:
        """What each "Hide the Least Engaging" modifier asked for and which
        step the page was cut at (see ``heat.used``)."""
        self.heat_rows = [dict(row) for row in rows]

    def fire(self, info) -> None:
        """The posts of the page that beat the page's 90th percentile."""
        self.fire_ids = set(info['ids'])

    def kitchen_cut(self, reason: str, before, after) -> None:
        kept = {id(item) for item in after}
        for item in before:
            if id(item) not in kept:
                self.kitchen_removed.setdefault(
                    str(item.get('id') or ''), reason)

    def guard_verdict(self, sid: str, broken) -> None:
        self.guard_broken[sid] = list(broken)

    def finish(self, ordered, score_map, counts_out, cursor_out,
               instances_out=None) -> None:
        self.ordered = [str(s.get('id') or '') for s in ordered]
        self.score_map = dict(score_map)
        self.counts_out = dict(counts_out or {})
        self.instances_out = dict(instances_out or {})
        self.identity['cursor_out'] = cursor_out

    def peeked(self) -> None:
        """This page is a peek of Smart New Posts, not a page shown."""
        self.peek = True

    def failed(self, message: str) -> None:
        self.error = redact(message)

    def cancelled(self) -> None:
        """The cook was cancelled or superseded: its page was never shown."""
        self.cancelled_flag = True

    def build(self) -> dict:
        """The schema-2 record. The text of each post goes to
        ``self.sidecar``, keyed by the post's ``text_ref``. It never
        raises: a page the hooks or this method could not capture is a
        record that says so (outcome failed, no posts), and the cook,
        which never reads it, is not affected."""
        try:
            record, sidecar = auditbuild.build_record(self)
        except Exception as error:
            auditbuild.capture_failed(self, 'build', error)
            self.sidecar = {}
            record, sidecar = auditbuild.build_record(self)
        self.sidecar.update(sidecar)
        return record


def _guarded(method):
    @functools.wraps(method)
    def hook(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as error:
            auditbuild.capture_failed(self, method.__name__, error)
            return None
    return hook


# Every hook the engine calls: none may raise into a cook (the audit is
# observation only), so a post the recorder cannot read costs the record,
# never the page.
for _name in ('clock', 'fetched', 'hashtag_fetched', 'pantry', 'counts',
              'follow_state', 'diet_decisions', 'reach_removed',
              'viewer_checked', 'trends_seen', 'judged_scores', 'heat_map',
              'factor', 'fresh_floor', 'fresh_fallback', 'relevance',
              'focus_unmatched', 'words', 'cluster', 'heat_steps', 'fire',
              'kitchen_cut',
              'guard_verdict', 'finish', 'failed', 'cancelled', 'peeked'):
    setattr(Recorder, _name, _guarded(getattr(Recorder, _name)))
del _name
