# SPDX-License-Identifier: GPL-3.0-or-later
"""The schema-2 audit record, built from what a ``audit.Recorder`` collected.

These functions read the recorder (``rec``: its trace of the pantry, its
recipe and everything the stages told it) and make the record; they never
write to the recorder. What building needs to keep (the text sidecar, and
which promises the recipe makes) lives in a ``Build`` made by
``build_record``, which returns the record and the sidecar.
``Recorder.build()`` calls it. The one thing written to the recorder is the
flag a page the hooks could not capture is given, by ``capture_failed``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from . import contract
from . import langguess
from . import sentiment as sentimentmod
from . import textutil
from .auditconst import (ENGINE_REV, SCHEMA, TEXT_CAP, recipe_hash,
                         stage_of, text_digest)
from .recipe import recipe_to_dict
from .relevance import focus_keywords
from .textutil import account_of, content_of, host_of_account

log = logging.getLogger('babble.feedchef')

_PANTRY_PROMISE = {'pantry_excluded_tag': 'hashtags_exclude',
                   'pantry_missing_included_tag': 'hashtags_include'}
# The order the stages ask the promises: the pantry's hashtags, the diet's
# six, then network reach.
_EVAL_ORDER = (contract.PROMISE_IDS[6:8] + contract.PROMISE_IDS[:6]
               + contract.PROMISE_IDS[8:])
_EVAL_AT = {name: at for at, name in enumerate(_EVAL_ORDER)}


class Build:
    """What one build of a record keeps: the text of each post for the
    sidecar (by ``text_ref``), and the promises the recipe makes (asked
    once). The recorder is only read."""

    __slots__ = ('sidecar', 'actives')

    def __init__(self) -> None:
        self.sidecar: dict[str, dict] = {}
        self.actives: set[str] | None = None


def _number(content: dict, *names: str) -> Any:
    for name in names:
        if content.get(name):
            return content[name]
    return 0


def _verdict(rec, status) -> str:
    reach = rec.recipe.pantry.network_reach
    if contract.reach_kind(reach) is None:
        return 'n/a'
    author = contract.author_id(status)
    if author and author == rec.self_id:
        return 'self'
    if rec.follow_ids is None:
        # Degree zero asks no list: anyone else is not let in.
        return 'unknown' if contract.reach_kind(reach) == 'follows' \
            else 'not_followed'
    return 'follows' if author in rec.follow_ids else 'not_followed'

def _facts(rec, build, status, ref: str) -> dict:
    content = content_of(status)
    text = contract.post_text(status, rec.native)
    spoiler = contract.spoiler_of(status)
    attachments = contract.attachments_of(status)
    kept = {'text': text[:TEXT_CAP], 'spoiler': spoiler[:TEXT_CAP]}
    build.sidecar[ref] = {**kept, 'text_truncated':
                          len(text) > TEXT_CAP or len(spoiler) > TEXT_CAP}
    boost = isinstance(status.get('reblog'), dict)
    wrapper = account_of(status, wrapper=True)
    extra = {}
    if _reads_words(rec):
        # What the relevance sort and the grouping read is the focus
        # text (it has the alt text and keeps what the contract's
        # text drops), so a replay needs it as it was.
        words = (rec.word_cache.text(status) if rec.word_cache
                 else textutil.focus_text(status))
        build.sidecar[ref].update(
            focus=words[:TEXT_CAP], focus_truncated=len(words) > TEXT_CAP)
        extra = {'focus_sha256': hashlib.sha256(
            words[:TEXT_CAP].encode('utf-8')).hexdigest()}
    # Only a recipe that reads sentiment guesses (and so loads
    # py3langid): the guess is what lets the engines judge a post.
    found = (langguess.guess(status)
             if sentimentmod.recipe_needs_sentiment(rec.recipe)
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

def _reads_words(rec) -> bool:
    """Whether the recipe has a stage that reads the words of the posts
    (the relevance sort, similarity grouping)."""
    return bool(focus_keywords(rec.recipe.kitchen)) or any(
        mod.type == 'similarity_cluster'
        for mod in rec.recipe.kitchen.modifiers)

def _rules(rec, build, sid: str, reason: str | None) -> dict[str, str]:
    """pass | fail | n/a for each promise, in the order the stages ran
    (pantry hashtags, diet, network reach): a promise after the one
    that removed the post was never asked (n/a), like one the recipe
    does not make. The guard asks every promise of a post it checks."""
    broken = rec.guard_broken.get(sid)
    failed = None
    if broken is None and reason:
        failed = _PANTRY_PROMISE.get(reason) or (
            reason[5:] if reason.startswith('diet:') else
            'network_reach' if reason == 'reach_not_followed' else None)
    done = _EVAL_AT[failed] if failed else len(_EVAL_ORDER)
    if build.actives is None:
        # One recipe per record: which promises it makes is asked once.
        build.actives = {promise.id for promise in contract.PROMISES
                         if promise.active(rec.recipe)}
    active = build.actives
    rules = {}
    for promise in contract.PROMISES:
        if promise.id not in active:
            rules[promise.id] = 'n/a'
        elif broken is not None:
            rules[promise.id] = 'fail' if promise.id in broken \
                else 'pass'
        else:
            at = _EVAL_AT[promise.id]
            rules[promise.id] = ('pass' if at < done else
                                 'fail' if at == done else 'n/a')
    return rules

def _removal(rec, sid: str, merged: set[str]) -> tuple[str, list] | None:
    if sid not in merged:
        return rec.trace.removed[sid], []
    if sid in rec.diet:
        return 'diet:' + rec.diet[sid], []
    if sid in rec.reach_dropped:
        return 'reach_not_followed', []
    if sid in rec.hidden:
        return 'viewer_hide', []
    if sid in rec.kitchen_removed:
        return rec.kitchen_removed[sid], []
    broken = rec.guard_broken.get(sid)
    if broken:
        return 'guard:' + broken[0], ['guard:' + b for b in broken[1:]]
    return None

def _sent(rec, sid: str, status) -> dict | None:
    if sid not in rec.judged:
        return None
    engine = sentimentmod.pick_engine(
        sentimentmod.effective_language(status))
    return {'engine': engine, 'score': rec.judged[sid]}

def _posts(rec, build) -> tuple[list[dict], dict[str, int]]:
    by_sid: dict[str, dict] = {}
    for status in rec.merged:
        by_sid.setdefault(str(status.get('id') or ''), status)
    order = [sid for sid in by_sid if sid]
    merged = set(order)
    for status in rec.raw:
        sid = str(status.get('id') or '')
        if sid and sid not in by_sid:
            by_sid[sid] = status
            order.append(sid)
    ranks = {sid: i for i, sid in enumerate(rec.ordered or [])}
    by_reason: dict[str, int] = {}
    posts = []
    for index, sid in enumerate(order):
        status = by_sid[sid]
        removal = _removal(rec, sid, merged)
        reason = removal[0] if removal else None
        if removal:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        origin, also = rec.trace.sources(sid)
        if sid in rec.factors:
            row = rec.factors[sid]
            score = {'base': row.get('base', 0.0),
                     'fresh': row.get('fresh', 1.0),
                     'sent': row.get('sent', 1.0),
                     'trend': row.get('trend', 1.0),
                     'final': rec.score_map.get(sid)}
            if sid in rec.floored:
                score['fresh_floor'] = True
            if rec.relevance_of is not None \
                    and sid in rec.relevance_of:
                score['relevance'] = rec.relevance_of[sid]
            if sid in ranks and rec.fire_ids is not None:
                score['fire'] = sid in rec.fire_ids
        else:
            score = None
        group = (rec.grouping.groups.get(sid)
                 if rec.grouping is not None else None)
        posts.append({
            'id': sid,
            'cid': str(content_of(status).get('id') or sid),
            'origin': origin, 'also_in': also,
            'facts': _facts(rec, build, status, 'p%d' % index),
            'follow': {'verdict': _verdict(rec, status)},
            'viewer': (rec.viewer or {}).get(sid),
            'rules': _rules(rec, build, sid, reason),
            'sent': _sent(rec, sid, status),
            'score': score,
            'removal': ({'stage': stage_of(reason), 'reason': reason,
                         'also': removal[1]} if removal else None),
            'rank': ranks.get(sid, -1),
        })
        if group is not None:
            posts[-1]['cluster'] = {'id': group[0], 'medoid': group[1]}
    return posts, by_reason


def _outcome(rec, failed: bool) -> dict:
    if rec.cancelled_flag and not failed:
        return {'status': 'cancelled', 'error': ''}
    error = rec.error
    if rec.broken:
        error = ('audit capture failed in %s' % rec.broken
                 + ('; ' + error if error else ''))
    if failed:
        return {'status': 'failed', 'error': error}
    return {'status': 'peek' if rec.peek else 'ok', 'error': error}


def build_record(rec) -> tuple[dict, dict[str, dict]]:
    """The record of a page, and the text sidecar that goes with it."""
    build = Build()
    failed = rec.ordered is None or bool(rec.broken)
    posts, by_reason = ([], {}) if failed else _posts(rec, build)
    selected = [] if failed else list(rec.ordered)
    instance, _sep, account = rec.account_key.rpartition('#')
    record = {
        'schema': SCHEMA, 'engine_rev': ENGINE_REV,
        **rec.identity,
        'app': {'version': rec.app_version},
        'recipe': {'id': rec.recipe.id,
                   'hash': recipe_hash(rec.recipe),
                   'snapshot': recipe_to_dict(rec.recipe)},
        'account': {'id': account or rec.self_id, 'instance': instance},
        'clock': {'now_utc': None if rec.now is None
                  else rec.now.isoformat()},
        'env': {'self_id': rec.self_id, 'native_bluesky': rec.native,
                'follow': rec.follow_info, 'trends': rec.trends,
                'local_heat': rec.tag_heat,
                'viewer_filter': rec.viewer is not None,
                'lang_guess': dict(zip(
                    ('status', 'reason'), langguess.status(),
                    strict=True), min_p=langguess.MIN_P,
                    min_chars=langguess.MIN_CHARS,
                    version=langguess.version())},
        'params': {'count': rec.count,
                   'author_counts_in': rec.counts_in,
                   'instance_counts_in': rec.instances_in},
        'sources': rec.sources,
        'outcome': _outcome(rec, failed),
        'summary': {
            'raw_fetched': len(rec.raw),
            'pantry_in': len(posts),
            'duplicates': rec.trace.duplicates,
            'no_id': rec.trace.no_id,
            'by_reason': by_reason,
            'selected': len(selected),
            'guard_checked': 0 if failed else len(rec.guard_broken)},
        'outputs': {'order': selected,
                    'author_counts_out': rec.counts_out,
                    'instance_counts_out': rec.instances_out},
        'posts': posts,
        **({'cluster': rec.grouping.record()}
           if rec.grouping is not None else {}),
        **({'focus_unmatched': True} if rec.unmatched else {}),
        **({'heat': rec.heat_rows} if rec.heat_rows else {}),
        **({'fresh_fallback': True} if rec.fallback_fresh else {}),
        **({'variant': rec.variant} if rec.variant else {}),
    }
    return record, build.sidecar


def capture_failed(rec, where: str, error: Exception) -> None:
    """A hook could not record: the audit gives up on this page and
    the cook goes on exactly as it would with the audit off."""
    if not rec.broken:
        rec.broken = '%s: %s' % (where, type(error).__name__)
        log.debug('feedchef: audit capture failed in %s', where,
                  exc_info=True)
