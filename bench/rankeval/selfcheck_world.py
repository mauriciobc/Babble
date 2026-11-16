# SPDX-License-Identifier: GPL-3.0-or-later
"""The self-tests of the synthetic world and of the rewrite, plus the runner."""

import datetime
import json
import os
import sqlite3
import tempfile
from rankeval.evaluate import (DAY,
    EVALUATION_HOUR,
    HOUR,
    candidates_at,
    day_moment,
    engagement_of,
    parse_moment)
from rankeval.rankstore import copy_database, database_path, open_database
from rankeval.selfcheck import (check_collect,
    check_gates,
    check_metrics,
    check_sign_test,
    check_split)
from rankeval.selfcheck_accounts import check_accounts
from rankeval.shared import SYNTHETIC_INSTANCE, iso_utc
from rankeval.synthetic import (Checks,
    LIKED_AUTHOR,
    MINUTE,
    REWRITE_DAY,
    WORLD_DAYS,
    WORLD_START,
    _NoNetwork,
    day_orderings,
    evaluate_summary,
    pin_zone,
    private_keys,
    private_values,
    record,
    rewrite_world,
    synthetic_world,
    world_tokens)
from src import affinity, mastodon



def check_world(checks, work, gates_path):
    """The five-day world: which days are judged, what the labels are,
    what a candidate's counters came from, and the whole-future rewrite."""
    pin_zone()
    world = synthetic_world()
    root = os.path.join(work, 'world')
    record(root, world,
           [WORLD_START + index * HOUR for index in range(WORLD_DAYS * 24)])
    summary, printed = evaluate_summary(
        root, os.path.join(work, 'world.json'), gates_path)
    days = summary['days']
    checks.equal('five days of hourly runs are judged on the three in the'
                 ' middle', [row['date'] for row in days],
                 ['2026-10-06', '2026-10-07', '2026-10-08'])
    checks.check('the first and last days have no coverage at one end',
                 '2026-10-05 06:00 local no-coverage' in printed
                 and '2026-10-09 06:00 local no-coverage' in printed)
    checks.equal('every judged day was labelled the same way',
                 sorted({row['labels'] for row in days}), [5])

    first = days[0]
    orderings = first['orderings']
    checks.equal('the day offered more candidates than the top 20 holds',
                 first['candidates'], 31)
    checks.equal("'newest first' leaves every label outside the top 20",
                 orderings['chrono']['recall20'], 0.0)
    checks.equal("but still recalls every label in the top 50",
                 orderings['chrono']['recall50'], 1.0)
    checks.equal('its top 20 is 20 distinct authors, none of them small',
                 (orderings['chrono']['authors20'],
                  orderings['chrono']['small20']), (20, 0.0))
    checks.equal('the affinity ordering puts every label in the top 20',
                 orderings['affinity_chrono']['recall20'], 1.0)
    checks.equal('as one author plus fifteen, five of them small',
                 (orderings['affinity_chrono']['authors20'],
                  orderings['affinity_chrono']['small20']), (16, 0.25))
    checks.equal('the popularity ordering finds none of them',
                 orderings['engagement']['recall20'], 0.0)
    checks.equal('the ranker without affinity finds none either',
                 orderings['rank_null']['recall20'], 0.0)
    checks.check('the ranker with affinity finds some, but not all: %r'
                 % orderings['rank_affinity']['recall20'],
                 0 < orderings['rank_affinity']['recall20'] < 1)
    # overlap20 is the popularity straw man's top 20 met by each ordering.
    # The engaged candidate is the oldest filler, so it is in the
    # popularity top 20 and outside chrono's.
    checks.equal('chrono overlaps the popularity top 20 in all but the'
                 ' candidate the counters lift into it',
                 orderings['chrono']['overlap20'], 19)
    checks.equal('and affinity-then-chrono in fifteen',
                 orderings['affinity_chrono']['overlap20'], 15)

    tokens = world_tokens(world)
    checks.equal('the summary carries no id, content, handle or url as a key',
                 private_keys(summary), [])
    checks.equal('nor as a value: no post id, handle or host',
                 private_values(summary, tokens), [])
    # The positive controls: each shape the reviewer added passed the
    # key-only rule, and each must be caught by the value rule.
    for key, value in (('first', 'liked-0-0'),
                       ('reader', '@synth@synth.example'),
                       ('where', SYNTHETIC_INSTANCE)):
        probe = json.loads(json.dumps(summary))
        probe['days'][0][key] = value
        checks.check('a %r value of %r is caught: %s'
                     % (key, value, private_values(probe, tokens)),
                     bool(private_values(probe, tokens)))

    no_gates, printed = evaluate_summary(
        root, os.path.join(work, 'nogates.json'),
        os.path.join(work, 'absent-gates.json'))
    checks.check('without a gates file it prints the metrics and refuses a'
                 ' verdict', 'gates not frozen, no verdict' in printed
                 and 'gates' not in no_gates)

    moment = datetime.datetime(2026, 10, 7, EVALUATION_HOUR,
                               tzinfo=datetime.timezone.utc)
    connection = open_database(database_path(SYNTHETIC_INSTANCE, root))
    try:
        affinities, candidates, ranked = day_orderings(connection, moment)
        observed_counters = {
            order: {row[0]: row[1:] for row in connection.execute(
                'SELECT post_id, favourites, reblogs FROM (SELECT *,'
                ' ROW_NUMBER() OVER (PARTITION BY post_id ORDER BY run_ts'
                ' %s) AS pick FROM observations WHERE run_ts <= ?)'
                ' WHERE pick = 1' % order, (iso_utc(moment),))}
            for order in ('DESC', 'ASC')}
    finally:
        connection.close()
    resolver = affinity.resolver(affinities, (), (),
                                 int(moment.timestamp() * 1000))
    strangers = set(affinities.author) | {'an author with no affinity'}
    checks.check('the liked author has a positive affinity: %r'
                 % affinities.author.get(LIKED_AUTHOR),
                 affinities.author.get(LIKED_AUTHOR, 0) > 0)
    checks.equal('affinity_chrono and rank_affinity read the same author'
                 ' signal, for every author',
                 {author: resolver.author_affinity(author)
                  for author in strangers},
                 {_author: affinity.affinity_signal(
                     affinities.author, _author) for _author in strangers})

    # The one candidate whose counters move without anyone engaging it.
    popular = 'filler-%d-0' % (moment.date() - WORLD_START.date()).days
    by_id = {str(status.get('id')): status for status in candidates}
    checks.check('the day still offers the candidate whose counters move',
                 popular in by_id)
    checks.equal('its counters are the last observation at or before T',
                 (by_id[popular]['favourites_count'],
                  by_id[popular]['reblogs_count']),
                 observed_counters['DESC'][popular])
    checks.check('and the first observation differs (%r, then %r), so first'
                 ' and last are told apart'
                 % (observed_counters['ASC'][popular],
                    observed_counters['DESC'][popular]),
                 observed_counters['ASC'][popular]
                 != observed_counters['DESC'][popular])
    checks.equal('the popularity ordering puts the most engaged candidate'
                 ' first', str(ranked['engagement'][0].get('id')), popular)
    # The straw man is favourites *plus* reblogs. Either alone would put
    # the same candidate first, so the order alone cannot say which.
    checks.equal('engagement is favourites plus reblogs, not one of them',
                 engagement_of({'favourites_count': 6, 'reblogs_count': 2}),
                 8)

    # The interior-gap branch of `covered`: the world is sampled hourly,
    # so only a hole can fail it. Three hours is over the limit, two is
    # inside GAP_TOLERANCE.
    # Inside the first judged day's window and no other judged day's:
    # deleting runs at 10:00 and 11:00 leaves a three-hour gap there,
    # while 24-hour-spaced windows mean a hole further along would be
    # reported twice.
    hole = datetime.datetime(2026, 10, 5, 10, tzinfo=datetime.timezone.utc)
    for name, holes, covered_by_hand in (
            ('two-hour hole', (hole,), True),
            ('three-hour hole', (hole, hole + HOUR), False)):
        gapped = os.path.join(work, 'gap-' + name.replace(' ', '-'))
        copy_database(root, gapped)
        connection = sqlite3.connect(
            database_path(SYNTHETIC_INSTANCE, gapped), isolation_level=None)
        try:
            connection.executemany('DELETE FROM runs WHERE ts = ?',
                                   [(iso_utc(at),) for at in holes])
        finally:
            connection.close()
        gap_summary, gap_printed = evaluate_summary(
            gapped, os.path.join(work, 'gap-%s.json' % name.replace(' ', '-')),
            gates_path)
        lost = '2026-10-06 06:00 local no-coverage' in gap_printed
        remaining = [row['date'] for row in gap_summary['days']]
        checks.equal('a %s in a judged day\'s window leaves it'
                     ' %s' % (name, 'judged' if covered_by_hand
                              else 'no-coverage'),
                     (lost, remaining),
                     (not covered_by_hand,
                      ['2026-10-06', '2026-10-07', '2026-10-08']
                      if covered_by_hand else
                      ['2026-10-07', '2026-10-08']))

    # The whole-future rewrite: every observation after the chosen day's
    # evaluation time engaged, every counter moved. The days *before* it
    # may not notice. The chosen day itself may: its labels are the
    # engagements in (T, T + 24 h], which is past the boundary by design
    # and is not leakage. `check_rewrite` is the sharper form: it puts
    # the boundary inside the compared day's future.
    chosen = datetime.date(2026, 10, 7)
    copied = os.path.join(work, 'rewritten')
    copy_database(root, copied)
    first_day = day_moment(datetime.date(2026, 10, 6))
    connection = sqlite3.connect(database_path(SYNTHETIC_INSTANCE, copied),
                                 isolation_level=None)
    try:
        rewritten = connection.execute(
            'UPDATE observations SET favourites = favourites + 1000,'
            ' reblogs = reblogs + 1000, replies = replies + 1000,'
            ' favourited = 1, reblogged = 1, bookmarked = 1'
            ' WHERE run_ts > ?', (iso_utc(day_moment(chosen)),)).rowcount
        kept = candidates_at(connection, first_day)
        engaged_ever = {row[0] for row in connection.execute(
            'SELECT DISTINCT post_id FROM observations'
            ' WHERE favourited OR reblogged OR bookmarked')}
        window = [str(post_id) for post_id, raw in connection.execute(
            'SELECT id, json FROM posts')
            if first_day - DAY <= parse_moment(
                json.loads(raw).get('created_at')) < first_day]
    finally:
        connection.close()
    checks.check('the rewrite changed observations the earlier days must'
                 ' not read (%d rows)' % rewritten, rewritten > 0)
    checks.equal('the day before it still has every candidate it had',
                 len(kept), 31)
    checks.equal('while a query without the time bound would find none of'
                 ' them (%d posts in its window)' % len(window),
                 sorted(set(window) - engaged_ever), [])

    after, _printed = evaluate_summary(
        copied, os.path.join(work, 'rewritten.json'), gates_path)

    def by_day(summary_):
        return {row['date']: json.dumps(row, sort_keys=True)
                for row in summary_['days']}

    was, now = by_day(summary), by_day(after)
    checks.equal('every day before the rewrite is byte for byte identical,'
                 ' day for day and metric for metric',
                 [day for day in was
                  if day < chosen.isoformat() and was[day] != now[day]], [])
    shifted = [day for day in now
               if day >= chosen.isoformat() and now[day] != was.get(day)]
    checks.check('and the days it moved differ: %s' % ', '.join(shifted),
                 shifted)

def check_rewrite(checks, work, gates_path):
    """The whole-future rewrite, with the boundary *inside* the compared
    day's future.

    `check_world` rewrites everything after the chosen day's 06:00 and
    compares the day before it, whose future is the 24 hours that
    rewrite does not touch -- so a bound one hour too far is invisible
    there. Here every candidate is already engaged at T + 30 min and the
    rewrite starts at T + 1 h: the labels cannot move, the day cannot
    move, and a query that reads anything after T does.
    """
    pin_zone()
    root = os.path.join(work, 'rewrite')
    moments = {WORLD_START + index * HOUR
               for index in range(WORLD_DAYS * 24)}
    moments.update(WORLD_START + index * DAY + EVALUATION_HOUR * HOUR
                   + 30 * MINUTE for index in range(WORLD_DAYS))
    record(root, rewrite_world(), sorted(moments))
    summary, _printed = evaluate_summary(
        root, os.path.join(work, 'rewrite.json'), gates_path)
    checks.equal('the rewrite world is judged on the three middle days',
                 [row['date'] for row in summary['days']],
                 ['2026-10-06', '2026-10-07', '2026-10-08'])
    chosen = (WORLD_START + REWRITE_DAY * DAY).date()
    first = {row['date']: row for row in summary['days']}[chosen.isoformat()]
    checks.equal('every candidate of the chosen day is labelled inside the'
                 ' hour after T, so the rewrite cannot move a label',
                 (first['labels'], first['candidates'],
                  first['labels_near_start']), (37, 37, 37))

    copied = os.path.join(work, 'rewrite-after')
    copy_database(root, copied)
    moment = day_moment(chosen)
    connection = sqlite3.connect(database_path(SYNTHETIC_INSTANCE, copied),
                                 isolation_level=None)
    try:
        inside = connection.execute(
            'SELECT COUNT(*) FROM observations WHERE run_ts > ?'
            ' AND run_ts < ?', (iso_utc(moment),
                                iso_utc(moment + DAY))).fetchone()[0]
        rewritten = connection.execute(
            'UPDATE observations SET favourites = favourites + 1000,'
            ' reblogs = reblogs + 1000, replies = replies + 1000,'
            ' favourited = 1, reblogged = 1, bookmarked = 1'
            ' WHERE run_ts > ?', (iso_utc(moment + HOUR),)).rowcount
    finally:
        connection.close()
    checks.check('the rewrite reaches into the compared day\'s future:'
                 ' %d observations inside (T, T + 24 h)' % inside, inside > 0)
    checks.check('and rewrote %d of them' % rewritten, rewritten > 0)

    after, _printed = evaluate_summary(
        copied, os.path.join(work, 'rewrite-after.json'), gates_path)

    def by_day(summary_):
        return {row['date']: json.dumps(row, sort_keys=True)
                for row in summary_['days']}

    was, now = by_day(summary), by_day(after)
    checks.equal('the day before it and the chosen day itself are byte for'
                 ' byte identical, day for day and metric for metric',
                 [day for day in was
                  if day <= chosen.isoformat() and was[day] != now[day]], [])
    later = (chosen + DAY).isoformat()
    checks.check('while the day after the boundary moved: %s' % later,
                 now.get(later) != was.get(later))

def self_test():
    """Check the harness against a world whose answer is known.

    Prints an ``ok - …`` line per check and a ``FAIL - …`` line for each
    that did not hold, and returns 1 if any failed. Every run goes through
    `FakeClient`, and `mastodon.MastodonClient` is replaced here by a
    class whose ``__init__`` raises -- so no `Soup.Session` is ever built,
    and a run that reached for the real client would fail loudly instead
    of quietly touching the network.
    """
    checks = Checks()
    # D3 judges a day at 06:00 *local*, so the world pins the local zone
    # rather than guess the machine's, and the dates above are the dates
    # the harness then judges. Every check calls `pin_zone` itself too, so
    # each one is also right when it is run alone.
    pin_zone()
    real = mastodon.MastodonClient
    mastodon.MastodonClient = _NoNetwork
    try:
        checks.check('the real client is replaced for the whole run',
                     mastodon.MastodonClient is _NoNetwork)
        with tempfile.TemporaryDirectory(prefix='rank-eval-') as work:
            check_metrics(checks)
            check_sign_test(checks)
            gates_path = check_gates(checks, work)
            check_split(checks, work)
            check_collect(checks, work)
            check_accounts(checks)
            check_world(checks, work, gates_path)
            check_rewrite(checks, work, gates_path)
    finally:
        mastodon.MastodonClient = real
    if checks.failures:
        print('%d checks, %d failed' % (checks.count, checks.failures))
        return 1
    print('%d checks, all passed' % checks.count)
    return 0
