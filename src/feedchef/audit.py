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

from . import contract
from . import langguess
from . import sentiment as sentimentmod
from .recipe import FeedRecipe, recipe_to_dict
from .relevance import focus_keywords
from . import textutil
from .textutil import account_of, content_of, host_of_account

log = logging.getLogger('babble.feedchef')

SCHEMA = 2
# Bumped by hand whenever the meaning of a stage changes; a replay refuses
# a record made by another revision unless it is forced.
ENGINE_REV = 3
# The stripped text kept per post in the sidecar.
TEXT_CAP = 2000

PANTRY_REASONS = ('pantry_excluded_tag', 'pantry_missing_included_tag')
# Revision 3 added cluster_cap and instance_cap.
FIXED_REASONS = frozenset(PANTRY_REASONS + (
    'reach_not_followed', 'viewer_hide', 'heat_cut', 'author_cap',
    'cluster_cap', 'instance_cap'))
_STAGES = {'reach_not_followed': 'reach', 'viewer_hide': 'viewer',
           'heat_cut': 'kitchen', 'author_cap': 'kitchen',
           'cluster_cap': 'kitchen', 'instance_cap': 'kitchen'}
_RANK = {'focus': 0, 'hashtag': 1, 'scope': 2}
_PANTRY_PROMISE = {'pantry_excluded_tag': 'hashtags_exclude',
                   'pantry_missing_included_tag': 'hashtags_include'}
# The order the stages ask the promises: the pantry's hashtags, the diet's
# six, then network reach.
_EVAL_ORDER = (contract.PROMISE_IDS[6:8] + contract.PROMISE_IDS[:6]
               + contract.PROMISE_IDS[8:])


def is_reason(reason: str) -> bool:
    """Whether *reason* is in the closed set of removal reasons."""
    if reason in FIXED_REASONS:
        return True
    kind, _sep, promise = reason.partition(':')
    if kind == 'diet':
        return promise in contract.DIET_IDS
    return kind == 'guard' and (promise in contract.PROMISE_IDS
                                or promise in contract.LIST_IDS)


def stage_of(reason: str) -> str:
    if reason.startswith('pantry_'):
        return 'pantry'
    return _STAGES.get(reason) or reason.partition(':')[0]


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


def text_digest(text: str, spoiler: str) -> str:
    """sha256 of the text and the spoiler exactly as the sidecar keeps
    them: the proof that the sidecar is the one this record was made with."""
    return hashlib.sha256(
        (text + '\0' + spoiler).encode('utf-8')).hexdigest()


def recipe_hash(recipe: FeedRecipe) -> str:
    """sha256 of the recipe as the same canonical JSON everywhere."""
    text = json.dumps(recipe_to_dict(recipe), sort_keys=True,
                      separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


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


def _number(content: dict, *names: str) -> Any:
    for name in names:
        if content.get(name):
            return content[name]
    return 0


class Recorder:
    """Collects one page's audit as the stages run; ``build()`` makes the
    record. All of it runs on the engine worker."""

    enabled = True

    def __init__(self, recipe: FeedRecipe, *, account_key: str = '',
                 self_id: str = '', cook_id: str = '', page_index: int = 0,
                 hop: int = 0, cursor_in: str | None = None, count: int = 0,
                 app_version: str = 'dev', session_id: str = ''):
        self.recipe = recipe
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
        self._follow_ids: set[str] | None = None
        self.reach_dropped: set[str] = set()
        self.hidden: set[str] = set()
        self.viewer: dict[str, str] | None = None
        self.sources: dict = {'scope': [], 'hashtag': {}, 'focus': {}}
        self.cancelled_flag = False
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
        self._follow_ids = set(ids)
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

    def failed(self, message: str) -> None:
        self.error = redact(message)

    def cancelled(self) -> None:
        """The cook was cancelled or superseded: its page was never shown."""
        self.cancelled_flag = True

    def _break(self, where: str, error: Exception) -> None:
        """A hook could not record: the audit gives up on this page and
        the cook goes on exactly as it would with the audit off."""
        if not self.broken:
            self.broken = '%s: %s' % (where, type(error).__name__)
            log.debug('feedchef: audit capture failed in %s', where,
                      exc_info=True)

    # -- the record ------------------------------------------------------

    def _verdict(self, status) -> str:
        reach = self.recipe.pantry.network_reach
        if contract.reach_kind(reach) is None:
            return 'n/a'
        author = contract.author_id(status)
        if author and author == self.self_id:
            return 'self'
        if self._follow_ids is None:
            # Degree zero asks no list: anyone else is not let in.
            return 'unknown' if contract.reach_kind(reach) == 'follows' \
                else 'not_followed'
        return 'follows' if author in self._follow_ids else 'not_followed'

    def _facts(self, status, ref: str) -> dict:
        content = content_of(status)
        text = contract.post_text(status, self.native)
        spoiler = contract.spoiler_of(status)
        attachments = contract.attachments_of(status)
        kept = {'text': text[:TEXT_CAP], 'spoiler': spoiler[:TEXT_CAP]}
        self.sidecar[ref] = {**kept, 'text_truncated':
                             len(text) > TEXT_CAP or len(spoiler) > TEXT_CAP}
        boost = isinstance(status.get('reblog'), dict)
        wrapper = account_of(status, wrapper=True)
        extra = {}
        if self._reads_words():
            # What the relevance sort and the grouping read is the focus
            # text (it has the alt text and keeps what the contract's
            # text drops), so a replay needs it as it was.
            words = (self.word_cache.text(status) if self.word_cache
                     else textutil.focus_text(status))
            self.sidecar[ref].update(
                focus=words[:TEXT_CAP], focus_truncated=len(words) > TEXT_CAP)
            extra = {'focus_sha256': hashlib.sha256(
                words[:TEXT_CAP].encode('utf-8')).hexdigest()}
        # Only a recipe that reads sentiment guesses (and so loads
        # py3langid): the guess is what lets the engines judge a post.
        found = (langguess.guess(status)
                 if sentimentmod.recipe_needs_sentiment(self.recipe)
                 else None)
        if found is not None:
            # A post that declares no language and has enough text was
            # guessed at (whatever the answer): the guess, for a replay
            # to rerun and for ``evaluate`` to check against the gates.
            extra['lang_guess'] = {'lang': found[0], 'p': found[1]}
        return {
            **extra,
            'host': host_of_account(wrapper),
            'created_w': status.get('created_at'),
            'created_c': content.get('created_at'),
            'fav': _number(content, 'favourites_count', 'favouritesCount'),
            'reblog': _number(content, 'reblogs_count', 'reblogsCount'),
            'reply': _number(content, 'replies_count', 'repliesCount'),
            'tags': [str((tag or {}).get('name') or '')
                     for tag in content.get('tags') or []],
            'lang': sentimentmod.language_of(status),
            'media': len(attachments),
            'alt': sum(1 for item in attachments
                       if str(item.get('description') or '').strip()),
            'sensitive': bool(content.get('sensitive')),
            'text_len': len(text), 'text_ref': ref,
            'text_sha256': text_digest(kept['text'], kept['spoiler']),
            'author_id': contract.author_id(status),
            'booster_id': (str(account_of(status, wrapper=True).get('id')
                               or '') if boost else None),
            'boost': boost,
        }

    def _reads_words(self) -> bool:
        """Whether the recipe has a stage that reads the words of the posts
        (the relevance sort, similarity grouping)."""
        return bool(focus_keywords(self.recipe.kitchen)) or any(
            mod.type == 'similarity_cluster'
            for mod in self.recipe.kitchen.modifiers)

    def _rules(self, sid: str, reason: str | None) -> dict[str, str]:
        """pass | fail | n/a for each promise, in the order the stages ran
        (pantry hashtags, diet, network reach): a promise after the one
        that removed the post was never asked (n/a), like one the recipe
        does not make. The guard asks every promise of a post it checks."""
        broken = self.guard_broken.get(sid)
        failed = None
        if broken is None and reason:
            failed = _PANTRY_PROMISE.get(reason) or (
                reason[5:] if reason.startswith('diet:') else
                'network_reach' if reason == 'reach_not_followed' else None)
        done = _EVAL_ORDER.index(failed) if failed else len(_EVAL_ORDER)
        rules = {}
        for promise in contract.PROMISES:
            if not promise.active(self.recipe):
                rules[promise.id] = 'n/a'
            elif broken is not None:
                rules[promise.id] = 'fail' if promise.id in broken \
                    else 'pass'
            else:
                at = _EVAL_ORDER.index(promise.id)
                rules[promise.id] = ('pass' if at < done else
                                     'fail' if at == done else 'n/a')
        return rules

    def _removal(self, sid: str, merged: set[str]) -> tuple[str, list] | None:
        if sid not in merged:
            return self.trace.removed[sid], []
        if sid in self.diet:
            return 'diet:' + self.diet[sid], []
        if sid in self.reach_dropped:
            return 'reach_not_followed', []
        if sid in self.hidden:
            return 'viewer_hide', []
        if sid in self.kitchen_removed:
            return self.kitchen_removed[sid], []
        broken = self.guard_broken.get(sid)
        if broken:
            return 'guard:' + broken[0], ['guard:' + b for b in broken[1:]]
        return None

    def _sent(self, sid: str, status) -> dict | None:
        if sid not in self.judged:
            return None
        engine = sentimentmod.pick_engine(
            sentimentmod.effective_language(status))
        return {'engine': engine, 'score': self.judged[sid]}

    def _posts(self) -> tuple[list[dict], dict[str, int]]:
        by_sid: dict[str, dict] = {}
        for status in self.merged:
            by_sid.setdefault(str(status.get('id') or ''), status)
        order = [sid for sid in by_sid if sid]
        merged = set(order)
        for status in self.raw:
            sid = str(status.get('id') or '')
            if sid and sid not in by_sid:
                by_sid[sid] = status
                order.append(sid)
        ranks = {sid: i for i, sid in enumerate(self.ordered or [])}
        by_reason: dict[str, int] = {}
        posts = []
        for index, sid in enumerate(order):
            status = by_sid[sid]
            removal = self._removal(sid, merged)
            reason = removal[0] if removal else None
            if removal:
                by_reason[reason] = by_reason.get(reason, 0) + 1
            origin, also = self.trace.sources(sid)
            if sid in self.factors:
                row = self.factors[sid]
                score = {'base': row.get('base', 0.0),
                         'fresh': row.get('fresh', 1.0),
                         'sent': row.get('sent', 1.0),
                         'trend': row.get('trend', 1.0),
                         'final': self.score_map.get(sid)}
                if sid in self.floored:
                    score['fresh_floor'] = True
                if self.relevance_of is not None \
                        and sid in self.relevance_of:
                    score['relevance'] = self.relevance_of[sid]
            else:
                score = None
            group = (self.grouping.groups.get(sid)
                     if self.grouping is not None else None)
            posts.append({
                'id': sid,
                'cid': str(content_of(status).get('id') or sid),
                'origin': origin, 'also_in': also,
                'facts': self._facts(status, 'p%d' % index),
                'follow': {'verdict': self._verdict(status)},
                'viewer': (self.viewer or {}).get(sid),
                'rules': self._rules(sid, reason),
                'sent': self._sent(sid, status),
                'score': score,
                'removal': ({'stage': stage_of(reason), 'reason': reason,
                             'also': removal[1]} if removal else None),
                'rank': ranks.get(sid, -1),
            })
            if group is not None:
                posts[-1]['cluster'] = {'id': group[0], 'medoid': group[1]}
        return posts, by_reason

    def build(self) -> dict:
        """The schema-2 record. The text of each post goes to
        ``self.sidecar``, keyed by the post's ``text_ref``. It never
        raises: a page the hooks or this method could not capture is a
        record that says so (outcome failed, no posts), and the cook,
        which never reads it, is not affected."""
        try:
            return self._build()
        except Exception as error:
            self._break('build', error)
            self.sidecar = {}
            return self._build()

    def _outcome(self, failed: bool) -> dict:
        if self.cancelled_flag and not failed:
            return {'status': 'cancelled', 'error': ''}
        error = self.error
        if self.broken:
            error = ('audit capture failed in %s' % self.broken
                     + ('; ' + error if error else ''))
        return {'status': 'failed' if failed else 'ok', 'error': error}

    def _build(self) -> dict:
        failed = self.ordered is None or bool(self.broken)
        posts, by_reason = ([], {}) if failed else self._posts()
        selected = [] if failed else list(self.ordered)
        instance, _sep, account = self.account_key.rpartition('#')
        return {
            'schema': SCHEMA, 'engine_rev': ENGINE_REV,
            **self.identity,
            'app': {'version': self.app_version},
            'recipe': {'id': self.recipe.id,
                       'hash': recipe_hash(self.recipe),
                       'snapshot': recipe_to_dict(self.recipe)},
            'account': {'id': account or self.self_id, 'instance': instance},
            'clock': {'now_utc': None if self.now is None
                      else self.now.isoformat()},
            'env': {'self_id': self.self_id, 'native_bluesky': self.native,
                    'follow': self.follow_info, 'trends': self.trends,
                    'local_heat': self.tag_heat,
                    'viewer_filter': self.viewer is not None,
                    'lang_guess': dict(zip(
                        ('status', 'reason'), langguess.status(),
                        strict=True), min_p=langguess.MIN_P,
                        min_chars=langguess.MIN_CHARS,
                        version=langguess.version())},
            'params': {'count': self.count,
                       'author_counts_in': self.counts_in,
                       'instance_counts_in': self.instances_in},
            'sources': self.sources,
            'outcome': self._outcome(failed),
            'summary': {
                'raw_fetched': len(self.raw),
                'pantry_in': len(posts),
                'duplicates': self.trace.duplicates,
                'no_id': self.trace.no_id,
                'by_reason': by_reason,
                'selected': len(selected),
                'guard_checked': 0 if failed else len(self.guard_broken)},
            'outputs': {'order': selected,
                        'author_counts_out': self.counts_out,
                        'instance_counts_out': self.instances_out},
            'posts': posts,
            **({'cluster': self.grouping.record()}
               if self.grouping is not None else {}),
            **({'focus_unmatched': True} if self.unmatched else {}),
        }


def _guarded(method):
    @functools.wraps(method)
    def hook(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as error:
            self._break(method.__name__, error)
            return None
    return hook


# Every hook the engine calls: none may raise into a cook (the audit is
# observation only), so a post the recorder cannot read costs the record,
# never the page.
for _name in ('clock', 'fetched', 'hashtag_fetched', 'pantry', 'counts',
              'follow_state', 'diet_decisions', 'reach_removed',
              'viewer_checked', 'trends_seen', 'judged_scores', 'heat_map',
              'factor', 'fresh_floor', 'relevance', 'focus_unmatched', 'words',
              'cluster',
              'kitchen_cut',
              'guard_verdict', 'finish', 'failed', 'cancelled'):
    setattr(Recorder, _name, _guarded(getattr(Recorder, _name)))
del _name
