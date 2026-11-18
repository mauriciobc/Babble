# SPDX-License-Identifier: GPL-3.0-or-later
"""A synthetic world with known answers, and the rows a fixture writes."""

import datetime
import io
import json
import os
import re
import time
import urllib.parse
from gi.repository import GLib
from rankeval.collect import collect
from rankeval.evaluate import (DAY,
    EVALUATION_HOUR,
    HOUR,
    ORDERINGS,
    affinity_at,
    candidates_at,
    evaluate,
    orderings_at,
    parse_moment,
    stored_maps)
from rankeval.shared import SYNTHETIC_INSTANCE, VIEWER_ACCT, VIEWER_ID
from src import affinity



class Checks:
    """`ok - …` lines, and `FAIL - …` for what did not hold."""

    def __init__(self):
        self.count = 0
        self.failures = 0

    def check(self, label, condition):
        self.count += 1
        if condition:
            print('ok - %s' % label)
        else:
            self.failures += 1
            print('FAIL - %s' % label)
        return bool(condition)

    def equal(self, label, got, want):
        same = got == want
        return self.check(label if same else
                          '%s: got %r, wanted %r' % (label, got, want), same)

    def close(self, label, got, want, tolerance=1e-9):
        near = got is not None and abs(got - want) <= tolerance
        return self.check(label if near else
                          '%s: got %r, wanted %r +/-%g'
                          % (label, got, want, tolerance), near)

class _NoNetwork:
    """Stands in for `mastodon.MastodonClient` while the self-test runs.

    Its ``__init__`` raises, so a run that reached for the real client
    fails here instead of opening a session. Patching `socket.socket`
    would prove nothing: libsoup does not use Python's sockets, so the
    test could pass while the network was used.
    """

    def __init__(self, *args, **kwargs):
        raise AssertionError('the self-test must not construct a client')

def pin_zone():
    """Judge days in UTC, for this helper on its own.

    D3 judges a day at 06:00 *local*, and the world's dates are written
    in UTC, so a check run under another zone compares the world's days
    against a 06:00 that has moved. `self_test` pins the zone for the
    whole run, but each helper calls this too: a helper that is only
    right because of the call order is a helper that fails when it is
    run alone. `time.tzset()` is how the C library is told.
    """
    os.environ['TZ'] = 'UTC'
    time.tzset()

LIKED_AUTHOR = 'synth-liked'

WORLD_START = datetime.datetime(2026, 10, 5, tzinfo=datetime.timezone.utc)

WORLD_DAYS = 5

LIKED_PER_DAY = 5

FILLERS_PER_DAY = 26

MINUTE = datetime.timedelta(minutes=1)

def synthetic_status(post_id, author, created, followers=500):
    """One status, in the shape Mastodon answers with.

    Every post carries the same text, tag and language on purpose: the
    ranker's diversity reranker then has nothing to prefer, so what moves
    an ordering here is freshness and the author signal, which are the
    two things these checks are about.
    """
    return {
        'id': post_id,
        'created_at': created.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'account': {
            'id': author, 'acct': author, 'username': author,
            'display_name': author, 'followers_count': followers,
            'following_count': 300, 'statuses_count': 900,
            'note': 'a synthetic account', 'fields': [],
        },
        'content': '<p>a synthetic post</p>',
        'tags': [{'name': 'synth'}],
        'language': 'en',
        'favourites_count': 0, 'reblogs_count': 0, 'replies_count': 0,
    }

class SyntheticWorld:
    """A feed, and when the reader engaged with it.

    ``statuses`` maps a post id to the status as the server first sent
    it, and ``likes`` maps the posts the reader favourites to the moment
    the favourite appears. A run at or after that moment shows the post
    ``favourited``, and the harness learns both the flag (a label, later)
    and the author signal (a favourite in ``/favourites``) from it.

    ``counters`` maps a post id to a schedule of ``(moment, favourites,
    reblogs)``: the last entry at or before the run's moment is what the
    server reports. It changes the counters of posts the reader never
    engaged, which is the only way a *candidate* can carry a counter --
    an engaged post is not a candidate by definition, so a world that
    only moves liked posts' counters leaves every candidate at zero and
    cannot tell "the last observation" from "the first".
    """

    def __init__(self, statuses, likes, own=(), counters=None):
        self.statuses = statuses
        self.likes = likes
        self.own = list(own)
        self.counters = counters or {}

    def _view(self, post_id, moment):
        status = self.statuses[post_id]
        seen = status
        for at, favourites, reblogs in self.counters.get(post_id, ()):
            if at <= moment:
                if seen is status:
                    seen = dict(status)
                seen['favourites_count'] = favourites
                seen['reblogs_count'] = reblogs
        at = self.likes.get(post_id)
        if at is None or at > moment:
            return seen
        if seen is status:
            seen = dict(status)
        seen['favourited'] = True
        seen['favourites_count'] = 1
        return seen

    def visible(self, moment):
        """The home timeline at *moment*: the last 48 hours, the way
        Mastodon caps it."""
        start = moment - 2 * DAY
        return [self._view(post_id, moment)
                for post_id, status in sorted(self.statuses.items())
                if start < parse_moment(status['created_at']) <= moment]

    def favourites(self, moment):
        """The reader's favourites at *moment*."""
        return [self._view(post_id, moment)
                for post_id, at in sorted(self.likes.items())
                if at <= moment]

    def own_statuses(self, moment):
        """The reader's own posts, boosts and replies written by *moment*."""
        return [status for status in self.own
                if parse_moment(status['created_at']) <= moment]

class FakeClient:
    """A duck-typed `mastodon.MastodonClient`: the calls `collect` makes,
    answered from a `SyntheticWorld`, with no socket anywhere.

    ``request_async`` calls back before it returns, which `call` allows --
    it only runs the loop when nothing answered. The account endpoint
    answers with the account **object**, the shape the real one answers
    with and the harness reads; answering with a one-element list is what
    hid an off-by-one during T1.1's review, so `check_collect` asserts a
    list is *rejected*, not accepted.
    """

    def __init__(self, world, moment, fail_at=None, limited=False):
        self.world = world
        self.moment = moment
        self.fail_at = fail_at
        self.limited = limited
        self.asked = []

    def rate_limited(self):
        return self.limited

    def retry_after_seconds(self):
        return 30

    def request_async(self, method, path, params=None, callback=None):
        self.asked.append((path, params))
        callback(self, None)

    def request_finish_paged(self, result):
        path, params = self.asked[-1]
        if path == self.fail_at:
            raise GLib.Error.new_literal(
                GLib.quark_from_string('rank-eval'), 'the request failed', 1)
        return self._answer(path, params), None, None

    def _answer(self, path, params):
        if path == '/accounts/verify_credentials':
            return {'id': VIEWER_ID, 'acct': VIEWER_ACCT,
                    'username': VIEWER_ACCT}
        if path == '/timelines/home':
            return self.world.visible(self.moment)
        if path == '/favourites':
            return self.world.favourites(self.moment)
        if path == '/accounts/%s/statuses' % VIEWER_ID:
            return self.world.own_statuses(self.moment)
        if path == '/accounts/relationships':
            return [{'id': account_id, 'following': True,
                     'followed_by': False}
                    for account_id in (params or {}).get('id[]') or ()]
        return []

class FakeAccountClient:
    """A duck-typed client for `token`, `follow`, `engage` and `inspect`.

    Reads answer from a small fixture and writes are recorded. The action
    names in *refuse* fail with a ``GLib.Error`` carrying the HTTP status
    a real server would answer with -- which is how the commands'
    refusal paths are seen without a server or a keyring. ``verify`` is a
    read; the rest are writes.

    The account body is the **object** the real endpoint answers with and
    `collect` reads (auth.py:535), never a one-element list.
    """

    def __init__(self, account_id=VIEWER_ID, following_count=0,
                 followers_count=0, statuses_count=0, follows=(),
                 following=(), requested=(), home=(), refuse=None,
                 limited_after=None, next_pages=()):
        self.account_id = str(account_id)
        self.following_count = following_count
        self.followers_count = followers_count
        self.statuses_count = statuses_count
        self.source_follows = [str(account) for account in follows]
        self.already = {str(account) for account in following}
        self.pending_requests = {str(account) for account in requested}
        self.home = list(home)
        self.refuse = dict(refuse or {})
        self.limited_after = limited_after
        # The parameters each page hands back for the next one, in order.
        self.next_pages = list(next_pages)
        self.asked = []
        self.asked_visibility = {}
        self.writes = 0
        self.followed = []
        self.requested = []
        self.pending = (None, None)

    def rate_limited(self):
        return (self.limited_after is not None
                and self.writes >= self.limited_after)

    def _account(self):
        return {'id': self.account_id, 'acct': 'managed',
                'username': 'managed',
                'following_count': self.following_count + len(self.followed),
                'followers_count': self.followers_count,
                'statuses_count': self.statuses_count}

    def _answer(self, path, params):
        if path == '/accounts/verify_credentials':
            return self._account()
        if path == '/timelines/home':
            return self.home
        if path == '/accounts/relationships':
            return [{'id': account_id,
                     'following': account_id in self.already,
                     'requested': account_id in self.pending_requests,
                     'followed_by': False}
                    for account_id in (params or {}).get('id[]') or ()]
        if path.startswith('/accounts/') and path.endswith('/following'):
            return [{'id': account_id} for account_id in self.source_follows]
        return []

    def _error(self, action):
        status = self.refuse.get(action)
        if status is None:
            return None
        return GLib.Error('%s refused by the test server' % action,
                          'mastodon-http-error-quark', status)

    def _finish(self, result):
        value, error = self.pending
        if error is not None:
            raise error
        return value

    def _read(self, action, path, params, callback):
        self.asked.append((action, path, params))
        self.pending = (self._answer(path, params), self._error(action))
        callback(self, None)

    def _write(self, action, argument, value):
        self.asked.append((action, argument))
        self.writes += 1
        self.pending = (value, self._error(action))

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        # The account actions arrive here too, from `src.api`.
        if method == 'GET' and path == '/accounts/verify_credentials':
            self._read('verify', path, params, callback)
            return
        followed = re.fullmatch(r'/accounts/([^/]+)/follow', path)
        if method == 'POST' and followed:
            self._follow(followed.group(1), callback)
            return
        # The status actions arrive here, from `src.api.statuses`.
        written = re.fullmatch(r'/statuses/([^/]+)/(favourite|reblog'
                               r'|bookmark)', path)
        if method == 'POST' and written:
            status_id, action = written.groups()
            if action == 'reblog':
                # The visibility is kept beside the call, so a check can
                # say the harness passed it on rather than dropping it.
                self.asked_visibility['reblog'] = (form or {}).get(
                    'visibility')
            flag = {'favourite': 'favourited', 'reblog': 'reblogged',
                    'bookmark': 'bookmarked'}[action]
            self._write(action, status_id, {'id': status_id, flag: True})
            callback(self, None)
            return
        if method == 'POST' and path == '/statuses':
            # A reply is a post with its parent named, from `src.api`.
            body = dict(json_body or {})
            text = body.pop('status', None)
            parent = body.pop('in_reply_to_id', None)
            self.asked.append(('reply', (str(parent), text, body)))
            self.writes += 1
            self.pending = ({'id': 'reply-1'}, self._error('reply'))
            callback(self, None)
            return
        self._read('request', path, params, callback)

    def request_finish(self, result):
        return self._finish(result)

    def request_finish_paged(self, result):
        # A page that has a next one hands back the parameters to ask
        # for it, which is how `inspect` knows the home timeline has
        # more than the first page and how `follow` walks the list. The
        # pages are per path, because one run asks for several.
        path = self.asked[-1][1] if self.asked else ''
        for index, (wanted, params) in enumerate(self.next_pages):
            if wanted == path:
                self.next_pages.pop(index)
                return self._finish(result), params, None
        return self._finish(result), None, None

    def _follow(self, account_id, callback):
        account_id = str(account_id)
        self.asked.append(('follow', account_id))
        self.writes += 1
        error = self._error('follow')
        if error is None:
            self.already.add(account_id)
            self.followed.append(account_id)
        self.pending = ({'id': account_id, 'following': error is None,
                         'requested': False}, error)
        callback(self, None)

def record(root, world, moments):
    """Record *world* once at each of *moments*, through the fake.

    `collect` calls its factory with (instance, token) and nothing else,
    so the moment a client is built for lives in a cell the loop fills
    in.
    """
    clock = {'now': moments[0]}
    quiet = io.StringIO()

    def factory(_instance, _token):
        return FakeClient(world, clock['now'])

    for moment in moments:
        clock['now'] = moment
        collect(SYNTHETIC_INSTANCE, make_client=factory, token='synthetic',
                now=moment, root=root, out=quiet)

def synthetic_world():
    """Five days of a feed, and labels planted on one author's posts.

    Each day at T has LIKED_PER_DAY posts by LIKED_AUTHOR and
    FILLERS_PER_DAY by distinct authors. Every liked post is older than
    every filler, so "newest first" puts the liked ones past the
    twentieth place -- while an ordering that reads the author signal
    puts them first. The likes that *label* a day's posts happen two
    hours after its T, and the likes that teach the author signal are
    yesterday's, so a day is judged on evidence that existed before it.

    LIKED_AUTHOR is a small account (100 followers) against fillers with
    500, which keeps `small20` a number that can move.

    One filler a day carries counters that grow while nobody engages it.
    It is a *candidate* on its own day, so its counters are the only way
    the day's numbers can tell "the last observation at or before T"
    from "the first one": with a zero everywhere, `ORDER BY run_ts ASC`
    and `engagement_of` returning a constant would both go unnoticed.
    """
    statuses, likes, counters = {}, {}, {}
    for index in range(WORLD_DAYS):
        moment = WORLD_START + index * DAY + EVALUATION_HOUR * HOUR
        for slot in range(LIKED_PER_DAY):
            post_id = 'liked-%d-%d' % (index, slot)
            statuses[post_id] = synthetic_status(
                post_id, LIKED_AUTHOR,
                moment - 8 * HOUR + slot * 10 * MINUTE, followers=100)
            likes[post_id] = moment + 2 * HOUR
        for slot in range(FILLERS_PER_DAY):
            post_id = 'filler-%d-%d' % (index, slot)
            statuses[post_id] = synthetic_status(
                post_id, 'filler-%d-%d' % (index, slot),
                moment - 7 * HOUR + (slot + 1) * 13 * MINUTE)
        counters['filler-%d-0' % index] = [
            (moment - 7 * HOUR, 0, 0),
            (moment - 2 * HOUR, 3, 1),
            (moment, 6, 2),
            (moment + HOUR, 9, 4),
        ]
    return SyntheticWorld(statuses, likes, counters=counters)

PLANT_T = WORLD_START + 2 * DAY + EVALUATION_HOUR * HOUR

PLANT_LEAK = 'plant-leak'

def plant_world(evidence_before):
    """The leak plant: one day, and one author whose *only* engagement is
    a favourite that the two variants place either side of T.

    Nothing else differs between the two databases, so whatever the day's
    numbers do with that one row shows up as a difference between them --
    which is how the check proves that reading it would change the top
    20, and that the harness does not read it.
    """
    statuses = {}
    for slot in range(26):
        post_id = 'plant-filler-%02d' % slot
        statuses[post_id] = synthetic_status(
            post_id, 'plant-filler-%02d' % slot,
            PLANT_T - 6 * HOUR + slot * 11 * MINUTE)
    statuses['plant-leak'] = synthetic_status(
        'plant-leak', PLANT_LEAK, PLANT_T - 12 * HOUR, followers=100)
    statuses['plant-leak-old'] = synthetic_status(
        'plant-leak-old', PLANT_LEAK, PLANT_T - 3 * DAY, followers=100)
    statuses['plant-engaged'] = synthetic_status(
        'plant-engaged', 'plant-mine', PLANT_T - 10 * HOUR, followers=100)
    statuses['plant-at-t'] = synthetic_status(
        'plant-at-t', 'plant-at-t-author', PLANT_T - 5 * HOUR,
        followers=100)
    statuses['plant-reply-target'] = synthetic_status(
        'plant-reply-target', 'plant-reply-author', PLANT_T - 11 * HOUR,
        followers=100)
    statuses['plant-boost-target'] = synthetic_status(
        'plant-boost-target', 'plant-boost-author', PLANT_T - 9 * HOUR,
        followers=100)
    # The three edges the plan pins, each on the side nothing else
    # covers: a post created at exactly T is outside the candidate
    # window [T - 24 h, T); a post first seen engaged at exactly
    # T + 24 h is inside the label window (T, T + 24 h]; and one first
    # seen at exactly T + 1 h is outside the hour `labels_near_start`
    # counts.
    statuses['plant-created-at-t'] = synthetic_status(
        'plant-created-at-t', 'plant-created-at-t-author', PLANT_T,
        followers=100)
    statuses['plant-at-day-end'] = synthetic_status(
        'plant-at-day-end', 'plant-at-day-end-author', PLANT_T - 2 * HOUR,
        followers=100)
    statuses['plant-near-edge'] = synthetic_status(
        'plant-near-edge', 'plant-near-edge-author', PLANT_T - 3 * HOUR,
        followers=100)
    # And one just past the near hour, so the hour's upper edge is
    # covered too: a label first seen at T + 1 h + 1 min is outside the
    # hour, and a label at exactly T + 1 h is inside it.
    statuses['plant-past-near'] = synthetic_status(
        'plant-past-near', 'plant-past-near-author', PLANT_T - 3 * HOUR,
        followers=100)
    likes = {
        # favourited before T: not a candidate, and not a label either
        'plant-engaged': PLANT_T - 2 * HOUR,
        # favourited exactly at T. The bounds are asymmetric on purpose:
        # a candidate is what no observation *at or before* T shows
        # engaged, so this one is not a candidate; a label is what was
        # first seen *after* T, so it is not a label; and affinity reads
        # what was first seen *before* T, so it is not evidence either.
        'plant-at-t': PLANT_T,
        # first seen engaged half an hour after T: a label, inside the
        # hour `labels_near_start` counts, and inside the hour a one-hour
        # leak would read.
        'plant-leak': PLANT_T + 30 * MINUTE,
        # the evidence, which is the one thing the variants move
        'plant-leak-old': (PLANT_T - 2 * HOUR if evidence_before
                           else PLANT_T + 30 * MINUTE),
        # first seen engaged at exactly T + 24 h: the label window is
        # (T, T + 24 h], so this one is a label, and the last hour of
        # it is inside.
        'plant-at-day-end': PLANT_T + DAY,
        # first seen engaged at exactly T + 1 h: a label, and inside the
        # hour `labels_near_start` counts -- the bound is inclusive.
        'plant-near-edge': PLANT_T + HOUR,
        # first seen engaged one minute later: a label, and outside the
        # hour.
        'plant-past-near': PLANT_T + HOUR + MINUTE,
    }
    # Two of the reader's own statuses, on the two sides of T. The label
    # window is (T, T + 24 h]: a reply at exactly T is outside it, and a
    # boost an hour later is inside. Both are read from `own` and matched
    # by content id, which is the path a favourite never takes.
    own = [
        {'id': 'plant-reply-at-t',
         'created_at': PLANT_T.strftime('%Y-%m-%dT%H:%M:%SZ'),
         'in_reply_to_id': 'plant-reply-target',
         'in_reply_to_account_id': 'plant-reply-author'},
        {'id': 'plant-boost-after-t',
         'account': {'id': VIEWER_ID},
         'created_at': (PLANT_T + HOUR).strftime('%Y-%m-%dT%H:%M:%SZ'),
         'reblog': statuses['plant-boost-target']},
    ]
    return SyntheticWorld(statuses, likes, own)

REWRITE_LEAK_AUTHOR = 'rewrite-leak-author'

REWRITE_LEAK_POSTS = 6

# The chosen day, as an index into WORLD_START's days. Its date is
# 2026-10-07, so the days around it are judged too.
REWRITE_DAY = 2

def rewrite_world():
    """Five days where every post is liked half an hour after its T.

    The whole-future rewrite turns every observation after a chosen
    moment into an engaged one, counters and all. For the identity
    assertion to say anything, the compared day's *labels* must survive
    that: labels are each post's first engaged observation, so every
    candidate here is already engaged at T + 30 min, and the rewrite
    starts at T + 1 h. The run at T + 30 min survives, the minimum does
    not move, and the day's numbers cannot change -- unless a query reads
    one row too many, which now sees the run at T + 30 min where every
    candidate is engaged and loses its whole candidate list.

    One author, REWRITE_LEAK_AUTHOR, appears only on the chosen day and
    is liked only there. Its affinity exists only if the favourites
    written in (T, T + 1 h) are read, so the same leak shows up in
    `affinity_at` as six posts entering a top 20 they were outside of.
    """
    statuses, likes = {}, {}
    for index in range(WORLD_DAYS):
        moment = WORLD_START + index * DAY + EVALUATION_HOUR * HOUR
        liked_at = moment + 30 * MINUTE
        for slot in range(LIKED_PER_DAY):
            post_id = 'liked-%d-%d' % (index, slot)
            statuses[post_id] = synthetic_status(
                post_id, LIKED_AUTHOR,
                moment - 8 * HOUR + slot * 10 * MINUTE, followers=100)
            likes[post_id] = liked_at
        for slot in range(FILLERS_PER_DAY):
            post_id = 'filler-%d-%d' % (index, slot)
            statuses[post_id] = synthetic_status(
                post_id, 'filler-%d-%d' % (index, slot),
                moment - 7 * HOUR + (slot + 1) * 13 * MINUTE)
            likes[post_id] = liked_at
        if index == REWRITE_DAY:
            for slot in range(REWRITE_LEAK_POSTS):
                post_id = 'leak-%d' % slot
                statuses[post_id] = synthetic_status(
                    post_id, REWRITE_LEAK_AUTHOR,
                    moment - 9 * HOUR + slot * 5 * MINUTE)
                likes[post_id] = liked_at
    return SyntheticWorld(statuses, likes)

def day_orderings(connection, moment):
    """The affinity maps, the candidates and the five orderings at
    *moment* -- the same steps `evaluate` takes for a day, so a check that
    uses it checks the harness's own path, not a copy of it."""
    viewer, following, mutuals = stored_maps(connection)
    affinities = affinity_at(connection, moment, viewer, mutuals)
    candidates = candidates_at(connection, moment)
    ranked = orderings_at(
        candidates, moment,
        affinity.resolver(affinities, (), (),
                          int(moment.timestamp() * 1000)),
        affinities, mutuals, following)
    return affinities, candidates, ranked

def evaluate_summary(root, path, gates_path):
    """`evaluate` on the database under *root*, as (summary, printed)."""
    printed = io.StringIO()
    evaluate(SYNTHETIC_INSTANCE, gates_path=gates_path, json_path=path,
             root=root, out=printed)
    with open(path, encoding='utf-8') as handle:
        return json.load(handle), printed.getvalue()

PRIVATE_KEYS = ('content', 'id', 'acct', 'url')

def private_keys(value, path=''):
    """Every key in *value* that names a post, an id, a handle or a URL."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in PRIVATE_KEYS:
                found.append('%s/%s' % (path, key))
            found.extend(private_keys(item, '%s/%s' % (path, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(private_keys(item, '%s[%d]' % (path, index)))
    return found

def private_values(value, forbidden, path=''):
    """Every string in *value* that names a post, an account or the host.

    The plan's privacy rule is written about key names; the design's
    (``Docs/timeline-algorithm.md`` §3.5 -- "shares only the metrics
    summary, never posts") is written about what a summary may carry at
    all. A key called ``first`` holding a post id satisfies the first
    rule and breaks the second, so both are checked.
    """
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(private_values(item, forbidden,
                                        '%s/%s' % (path, key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(private_values(item, forbidden,
                                        '%s[%d]' % (path, index)))
    elif isinstance(value, str):
        for token in forbidden:
            if token and token in value:
                found.append('%s: %r' % (path, value))
                break
    return found

def world_tokens(world):
    """Every post id, author id, handle and the URL of *world*'s host."""
    host = urllib.parse.urlsplit(SYNTHETIC_INSTANCE).hostname
    # The bare handle needs no token of its own: `synth` is a substring
    # of `@synth@synth.example`, and the check looks for substrings.
    tokens = {SYNTHETIC_INSTANCE, VIEWER_ID,
              '@%s@%s' % (VIEWER_ACCT, host)}
    for post_id, status in world.statuses.items():
        account = status['account']
        tokens.add(post_id)
        tokens.add(account['id'])
        tokens.add('@%s@%s' % (account['acct'], host))
    return tokens

# The gates of §4, as a fixture file. The harness reads them through
# `load_gates` like any other, so the checks exercise the path the real
# run takes. The frozen file is the maintainer's; this only pins the keys
# it must carry.
FIXTURE_GATES = {
    'version': 1,
    'alpha': 0.05,
    'min_nontied_days': 11,
    'a1_k': 20,
    'a1_min_mean_ratio': 1.25,
    'a2_k': 20,
    'a2_max_author_drop': 2,
    'a2_min_small_share_ratio': 0.8,
    'min_accounts_passing': 2,
}

def fixture_rows(scores, second=None, breadth=None):
    """Rows `judge` can read: one a day, each a mapping of ordering to
    recall@20.

    ``second`` overrides recall@50 per ordering. The gates must read
    ``a1_k``'s metric, and two metrics that always agreed would let them
    read either one.

    ``breadth`` overrides ``(authors20, small20)`` per ordering -- the
    two numbers A2 reads. ``None`` stands for a mean that is missing, so
    A2's INCONCLUSIVE branch can be reached.

    Every other metric is held equal, so a verdict that moves is the one
    the gate decided.
    """
    rows = []
    for index, score in enumerate(scores):
        orderings = {}
        for name in ORDERINGS:
            value = score.get(name, 0.0)
            authors, share = (breadth or {}).get(name, (20, 0.5))
            orderings[name] = {
                'recall20': value,
                'recall50': (second or {}).get(name, value),
                'authors20': authors, 'small20': share, 'overlap20': 20}
        rows.append({'date': '2026-09-%02d' % (index + 1),
                     't': '2026-09-%02dT09:00:00.000000Z' % (index + 1),
                     'candidates': 25, 'labels': 5, 'labels_near_start': 0,
                     'orderings': orderings})
    return rows

def summary_record(gate_a, ordering, use_affinity, shipped, a1, a2):
    """One account's record, as `evaluate --json` writes it."""
    return {'version': 1, 'days': [], 'means': {}, 'gates': {},
            'gate_a': gate_a, 'ordering': ordering,
            'use_affinity': use_affinity, 'shipped': shipped,
            'shipped_a1': a1, 'shipped_a2': a2}
