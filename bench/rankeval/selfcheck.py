# SPDX-License-Identifier: GPL-3.0-or-later
"""The self-tests of the metrics, the gates, the split and the collector."""

import datetime
import io
import os
import stat
from rankeval.collect import collect, gather
from rankeval.evaluate import (DAY,
    EVALUATION_HOUR,
    HOUR,
    aggregate,
    candidates_at,
    judge,
    labels_at,
    load_gates,
    metrics_at,
    near_labels,
    parse_moment,
    print_gates,
    sign_test,
    small_authors)
from rankeval.rankstore import (data_dir,
    database_path,
    open_database,
    stored_rows,
    write_json)
from rankeval.shared import SYNTHETIC_INSTANCE, VIEWER_ID, iso_utc
from rankeval.synthetic import (FIXTURE_GATES,
    FakeClient,
    MINUTE,
    PLANT_LEAK,
    PLANT_T,
    WORLD_START,
    day_orderings,
    fixture_rows,
    pin_zone,
    plant_world,
    record,
    summary_record,
    synthetic_status,
    synthetic_world)



def check_metrics(checks):
    """Recall@k and the other four metrics, on an ordering worked out by
    hand.

    The labels sit on the boundaries themselves: index 19 is the last
    post inside the top 20 and 20 the first outside it, and index 49 is
    the last inside the top 50. A cut one short or one long moves a
    label, so `ids[:k-1]`, `ids[:k]` and `ids[:k+1]` give three different
    answers. The row is longer than 50 posts, or the top 50 would hold
    every post and its cut could not be reached.
    """
    pin_zone()
    posts = [synthetic_status('p%02d' % index, 'author-%02d' % index,
                              WORLD_START) for index in range(60)]
    posts[1]['account']['followers_count'] = 10
    posts[59]['account']['followers_count'] = 10
    small = small_authors(posts)
    checks.equal('the small-account set is the two authors below the median',
                 sorted(small), ['author-01', 'author-59'])
    row = metrics_at(posts, {'p02', 'p19', 'p20', 'p49'}, small,
                     {'p00', 'p01', 'p59'})
    checks.equal('recall@20 counts index 19 and not index 20, so 2 of 4',
                 row['recall20'], 2 / 4)
    checks.equal('recall@50 counts index 49, so all 4',
                 row['recall50'], 4 / 4)
    checks.equal('the top 20 holds 20 distinct authors', row['authors20'], 20)
    checks.equal('1 of 20 is a small account, so the share is 0.05',
                 row['small20'], 0.05)
    checks.equal('2 of the top 20 are in the engagement order',
                 row['overlap20'], 2)
    checks.equal('a day with no labels scores no recall',
                 metrics_at(posts, set(), small, set())['recall20'], None)

def check_sign_test(checks):
    """The anchors §4 pins, to five decimal places."""
    checks.close('the sign test gives p = 0.02869 for 11 of 14',
                 sign_test(11, 14), 0.02869, 1e-5)
    checks.close('and p = 0.08978 for 10 of 14',
                 sign_test(10, 14), 0.08978, 1e-5)
    checks.equal('no non-tied days at all is p = 1', sign_test(0, 0), 1.0)

def check_gates(checks, work):
    """PASS, FAIL and INCONCLUSIVE on a fixture gates file, and the rule
    that the ordering which ships must pass on its own numbers.

    Each gate is asserted on the fixture where *its own* condition is the
    only thing that can move the verdict. That is what separates the four
    baselines the plan names: a fixture where `chrono`, `rank_null` and
    `affinity_chrono` score alike cannot tell A3 from A4, and one where
    `recall20` and `recall50` agree cannot tell the metric the gates read.
    """
    pin_zone()
    gates_path = write_json(os.path.join(work, 'fixture-gates.json'),
                            FIXTURE_GATES)
    gates = load_gates(gates_path)
    checks.equal('the fixture gates file reads back as written',
                 gates, FIXTURE_GATES)
    frozen = load_gates()
    checks.check('the frozen gates file is there, with the same keys',
                 frozen is not None and set(frozen) == set(FIXTURE_GATES))

    winning = [{'rank_affinity': 0.8 if day < 11 else 0.4,
                'rank_null': 0.4, 'chrono': 0.4, 'affinity_chrono': 0.4}
               for day in range(14)]
    decision = judge(fixture_rows(winning), gates)
    checks.equal('A1 passes on 11 wins out of 14',
                 decision['a1']['verdict'], 'PASS')
    checks.equal('its sample is the 11 days that were not ties',
                 decision['a1']['nontied'], 11)
    checks.equal('and its p is the sign test of 11 wins in 11 days',
                 decision['a1']['p'], sign_test(11, 11))
    checks.equal('A2 passes when the top 20 keeps its authors and its'
                 ' small-account share', decision['a2']['verdict'], 'PASS')
    checks.equal('A3 passes: affinity beats no affinity',
                 decision['a3']['verdict'], 'PASS')
    checks.equal('A4 passes: the ranker beats affinity-then-chrono',
                 decision['a4']['verdict'], 'PASS')
    checks.equal('the ordering that was checked in its own right is rank'
                 ' with affinity', decision['shipped'], 'rank_affinity')
    checks.equal('Gate A passes, shipping rank with affinity',
                 (decision['gate_a'], decision['ordering'],
                  decision['use_affinity']), ('PASS', 'rank', True))

    # A2: the two numbers it reads, each its own way of failing, and the
    # missing mean it cannot decide. Without these, deleting A2's
    # condition leaves every verdict where it was.
    same_order = [{'rank_affinity': 0.8 if day < 11 else 0.4,
                   'rank_null': 0.4, 'chrono': 0.4, 'affinity_chrono': 0.4}
                  for day in range(14)]

    def breadth_of(rank_authors, rank_share):
        return judge(fixture_rows(
            same_order,
            breadth={'rank_affinity': (rank_authors, rank_share)}), gates)

    checks.equal('A2 allows a drop of exactly the two authors the gates'
                 ' name', breadth_of(18, 0.5)['a2']['verdict'], 'PASS')
    checks.equal('and a small-account share of exactly 0.8x',
                 breadth_of(20, 0.4)['a2']['verdict'], 'PASS')
    narrow = breadth_of(17, 0.5)
    checks.equal('A2 fails when the top 20 loses three authors',
                 narrow['a2']['verdict'], 'FAIL')
    checks.equal('and Gate A fails on A2 alone, A1 having passed',
                 (narrow['a1']['verdict'], narrow['gate_a']),
                 ('PASS', 'FAIL'))
    checks.equal('A2 fails when the small-account share is below 0.8x',
                 breadth_of(20, 0.3)['a2']['verdict'], 'FAIL')
    blind = breadth_of(None, None)
    checks.equal('A2 is INCONCLUSIVE when a mean is missing',
                 blind['a2']['verdict'], 'INCONCLUSIVE')
    checks.equal('and Gate A is INCONCLUSIVE, not a pass',
                 (blind['a1']['verdict'], blind['gate_a']),
                 ('PASS', 'INCONCLUSIVE'))

    # A2 is measured against *chrono*, not against rank_null. The two
    # must be told apart, or the gate could be scored on the wrong
    # baseline and nobody would know. So one fixture, with the two
    # baselines given different breadths: the verdict it reaches says
    # which one the gate read.
    apart = judge(fixture_rows(
        same_order,
        breadth={'rank_affinity': (18, 0.5), 'chrono': (15, 0.4),
                 'rank_null': (20, 0.5)}), gates)
    checks.equal('A2 measures against chrono, not rank_null: it passes on'
                 ' chrono\'s breadth',
                 apart['a2']['verdict'], 'PASS')
    checks.equal('and chrono\'s breadth is the one it read',
                 (apart['a2']['chrono_authors'], apart['a2']['chrono_small_share']),
                 (15, 0.4))

    # A1 is scored on recall@`a1_k`, and only on it. Here the ranker wins
    # every day on recall@20 and loses every day on recall@50.
    crossed = [{'rank_affinity': 0.8, 'rank_null': 0.0, 'chrono': 0.4,
                'affinity_chrono': 0.0} for _day in range(14)]
    decision = judge(fixture_rows(
        crossed, second={'rank_affinity': 0.2, 'chrono': 0.6}), gates)
    checks.equal('A1 reads recall@a1_k: the other metric turns every win'
                 ' into a loss',
                 (decision['a1']['verdict'], decision['a1']['wins']),
                 ('PASS', 14))

    # A1's two halves: the sign test alone can pass while the mean-ratio
    # floor fails, and chrono can recall nothing at all.
    thin_gain = judge(fixture_rows(
        [{'rank_affinity': 0.45, 'rank_null': 0.4, 'chrono': 0.4,
          'affinity_chrono': 0.4} for _day in range(14)]), gates)['a1']
    checks.check('A1 fails on the mean ratio alone: %r with %d wins, p=%r'
                 % (thin_gain['mean_ratio'], thin_gain['wins'],
                    thin_gain['p']),
                 thin_gain['verdict'] == 'FAIL'
                 and thin_gain['wins'] == 14
                 and thin_gain['p'] <= gates['alpha']
                 and thin_gain['mean_ratio'] < gates['a1_min_mean_ratio'])
    nothing = judge(fixture_rows(
        [{'rank_affinity': 0.2, 'rank_null': 0.0, 'chrono': 0.0,
          'affinity_chrono': 0.0} for _day in range(14)]), gates)['a1']
    checks.equal('A1 passes when chrono recalled nothing and rank did not',
                 nothing['verdict'], 'PASS')
    checks.equal('and its mean ratio is undefined, not a division by zero',
                 nothing['mean_ratio'], None)
    # The floor is a *minimum*: a ratio of exactly 1.25 passes. A gate
    # that asked for strictly more would ship a stricter rule than the
    # frozen file names.
    exactly = judge(fixture_rows(
        [{'rank_affinity': 0.5, 'rank_null': 0.4, 'chrono': 0.4,
          'affinity_chrono': 0.4} for _day in range(14)]), gates)['a1']
    checks.equal('A1 accepts a mean ratio of exactly the minimum',
                 (exactly['verdict'], exactly['mean_ratio']),
                 ('PASS', gates['a1_min_mean_ratio']))

    losing = [{'rank_affinity': 0.4, 'rank_null': 0.2, 'chrono': 0.5,
               'affinity_chrono': 0.2} for _day in range(14)]
    decision = judge(fixture_rows(losing), gates)
    checks.equal('A1 fails when the ranker loses to chrono',
                 decision['a1']['verdict'], 'FAIL')
    checks.equal('and Gate A fails with it', decision['gate_a'], 'FAIL')

    few = [{'rank_affinity': 0.8, 'rank_null': 0.4, 'chrono': 0.4,
            'affinity_chrono': 0.4} for _day in range(5)]
    decision = judge(fixture_rows(few), gates)
    checks.equal('A1 is INCONCLUSIVE below min_nontied_days',
                 decision['a1']['verdict'], 'INCONCLUSIVE')
    checks.equal('and Gate A is INCONCLUSIVE: not a pass, not a fail',
                 decision['gate_a'], 'INCONCLUSIVE')
    checks.equal('A4 below min_nontied_days is INCONCLUSIVE too',
                 decision['a4']['verdict'], 'INCONCLUSIVE')
    checks.equal('and an inconclusive A4 ships the simple ordering',
                 decision['ordering'], 'affinity_chrono')

    # A3 asks whether affinity beats *no affinity*; its baseline is
    # `rank_null`, not `chrono`. Level with rank_null is INCONCLUSIVE
    # however far behind chrono the ranker leaves it.
    level = [{'rank_affinity': 0.8, 'rank_null': 0.8, 'chrono': 0.2,
              'affinity_chrono': 0.2} for _day in range(14)]
    decision = judge(fixture_rows(level), gates)
    checks.equal('A3 compares against rank_null: level with it is'
                 ' INCONCLUSIVE', decision['a3']['verdict'], 'INCONCLUSIVE')
    checks.equal('so affinity is not used, though chrono trails by 0.6',
                 decision['use_affinity'], False)
    checks.equal('and the ordering that ships is rank without affinity',
                 decision['shipped'], 'rank_null')

    # A4 asks whether rank() beats *affinity, then chronological*; its
    # baseline is `affinity_chrono`, not `chrono`. Trailing
    # affinity_chrono while beating chrono is what tells them apart.
    overtaken = [{'rank_affinity': 0.5, 'rank_null': 0.1, 'chrono': 0.4,
                  'affinity_chrono': 0.9} for _day in range(14)]
    decision = judge(fixture_rows(overtaken), gates)
    checks.equal('A4 compares against affinity_chrono, so beating chrono'
                 ' is not enough', decision['a4']['verdict'], 'FAIL')
    checks.equal('and hands the ordering over', decision['ordering'],
                 'affinity_chrono')

    # A4 did not select `rank`, so `affinity_chrono` is what ships -- and
    # it fails A1 against chrono on its own numbers. Gate A must be FAIL
    # even though the ranker's A3 verdict was better.
    handed_over = [{'rank_affinity': 0.4, 'rank_null': 0.4, 'chrono': 1.0,
                    'affinity_chrono': 0.7} for _day in range(14)]
    decision = judge(fixture_rows(handed_over), gates)
    checks.equal('A4 hands the ordering to affinity-then-chrono',
                 (decision['a4']['verdict'], decision['ordering']),
                 ('FAIL', 'affinity_chrono'))
    checks.equal('which then fails A1 on its own numbers',
                 decision['shipped_a1']['verdict'], 'FAIL')
    checks.equal('so Gate A is FAIL, not a pass on another ordering',
                 decision['gate_a'], 'FAIL')
    record_text = io.StringIO()
    print_gates(decision, record_text)
    printed = record_text.getvalue()
    checks.check('the H1 record says Gate A: FAIL', 'Gate A: FAIL' in printed)
    checks.check('and names affinity-then-chrono as the ordering',
                 'ORDERING: affinity_chrono' in printed)
    checks.check('and leaves affinity off', 'USE_AFFINITY: false' in printed)

    # The mirror image: the ranker fails A1, affinity_chrono passes it,
    # and A4 hands the ordering over. Consulting the ranker's verdicts
    # instead of the shipped ordering's would fail Gate A.
    saved = [{'rank_affinity': 0.2, 'rank_null': 0.1, 'chrono': 0.4,
              'affinity_chrono': 0.9} for _day in range(14)]
    decision = judge(fixture_rows(saved), gates)
    checks.equal('the ranker fails A1 here', decision['a1']['verdict'],
                 'FAIL')
    checks.equal('while affinity-then-chrono passes it',
                 decision['shipped_a1']['verdict'], 'PASS')
    checks.equal('so Gate A is PASS on the shipped ordering\'s numbers',
                 (decision['shipped'], decision['gate_a']),
                 ('affinity_chrono', 'PASS'))

    # The same, for A2: the ordering that ships is too narrow in the top
    # 20, while the ranker is broad enough. Consulting the ranker's A2
    # instead of the shipped ordering's would pass Gate A on the wrong
    # ordering's breadth -- and `aggregate` counts an account by the
    # verdicts it shipped.
    saved_narrow = [{'rank_affinity': 0.2, 'rank_null': 0.1, 'chrono': 0.4,
                     'affinity_chrono': 0.9} for _day in range(14)]
    decision = judge(fixture_rows(
        saved_narrow, breadth={'affinity_chrono': (17, 0.5),
                               'rank_affinity': (20, 0.5)}), gates)
    checks.equal('the shipped ordering fails A2 on its own breadth',
                 decision['shipped_a2']['verdict'], 'FAIL')
    checks.equal('while the ranker passes it',
                 decision['a2']['verdict'], 'PASS')
    checks.equal('so Gate A is FAIL, not a pass on the rankers breadth',
                 decision['gate_a'], 'FAIL')

    passed = summary_record('PASS', 'rank', True, 'rank_affinity',
                            'PASS', 'PASS')
    failed = summary_record('FAIL', 'rank', True, 'rank_affinity',
                            'FAIL', 'PASS')
    # A record that says it shipped one ordering but was judged as another
    # is exactly the hole the review of T1.2 found: a majority must not
    # name an ordering that no account shipped and passed.
    stray = summary_record('PASS', 'rank', True, 'affinity_chrono',
                           'PASS', 'PASS')
    paths = {
        'one': write_json(os.path.join(work, 'account-one.json'), passed),
        'two': write_json(os.path.join(work, 'account-two.json'),
                          dict(passed)),
        'failing': write_json(os.path.join(work, 'account-failing.json'),
                              failed),
        'stray': write_json(os.path.join(work, 'account-stray.json'), stray),
    }
    printed = io.StringIO()
    aggregate([paths['one'], paths['two']], gates_path=gates_path, out=printed)
    checks.check('two accounts that shipped the ordering and passed make'
                 ' Gate A PASS', 'Gate A: PASS' in printed.getvalue())
    printed = io.StringIO()
    aggregate([paths['one'], paths['failing']], gates_path=gates_path,
              out=printed)
    checks.check('one account failing makes it FAIL',
                 'Gate A: FAIL' in printed.getvalue())
    printed = io.StringIO()
    aggregate([paths['stray']], gates_path=gates_path, out=printed)
    checks.check('a record that shipped another ordering cannot make it PASS',
                 'Gate A: INCONCLUSIVE' in printed.getvalue())

    # The same account twice is one account counted twice, and the gate
    # would read it as two.
    printed = io.StringIO()
    code = aggregate([paths['one'], paths['one']], gates_path=gates_path,
                     out=printed)
    checks.equal('the same summary given twice is refused',
                 (code, 'Gate A: PASS' in printed.getvalue()), (2, False))
    return gates_path

def check_split(checks, work):
    """A post engaged before T is not a candidate, an engagement after T
    does not reach the affinity at T -- and the pair of databases shows
    that reading it *would* change the top 20.

    The runs are placed on the boundaries themselves, not between them: a
    run at exactly T and another half an hour later are what tell `<= T`
    from `< T` and `< T` from `< T + 1 h`. A world that only sampled T -
    2 h and T + 2 h would leave all three of those cuts looking alike.
    """
    pin_zone()
    answers = {}
    moments = (PLANT_T - 13 * HOUR, PLANT_T - 2 * HOUR, PLANT_T,
               PLANT_T + 30 * MINUTE, PLANT_T + HOUR, PLANT_T + HOUR + MINUTE,
               PLANT_T + 2 * HOUR, PLANT_T + DAY)
    for name, evidence_before in (('bounded', False), ('control', True)):
        root = os.path.join(work, 'split-' + name)
        record(root, plant_world(evidence_before), moments)
        connection = open_database(database_path(SYNTHETIC_INSTANCE, root))
        try:
            affinities, candidates, ranked = day_orderings(connection, PLANT_T)
            labels, near = labels_at(connection, PLANT_T, candidates)
            seen = connection.execute(
                'SELECT post_id, MIN(run_ts) FROM observations'
                ' WHERE favourited OR reblogged OR bookmarked'
                ' GROUP BY post_id').fetchall()
            # The label window's lower cut on its own. At T the post
            # engaged exactly at T is already excluded from the
            # candidates, so its `seen == T` case is never reached there;
            # a candidate list taken half an hour earlier does include
            # it, and "after T" must still exclude it.
            earlier = labels_at(
                connection, PLANT_T,
                candidates_at(connection, PLANT_T - 30 * MINUTE))[0]
            near_set = near_labels(connection, PLANT_T, candidates)
        finally:
            connection.close()
        answers[name] = {
            'author': affinities.author,
            'candidates': [str(status.get('id')) for status in candidates],
            'top': [str(status.get('id'))
                    for status in ranked['rank_affinity'][:20]],
            'labels': labels,
            'near': near,
            'near_labels': near_set,
            'first': dict(seen),
            'earlier_labels': earlier,
        }

    bounded, control = answers['bounded'], answers['control']
    checks.check('a post engaged before T is not a candidate',
                 'plant-engaged' not in bounded['candidates'])
    checks.check('a post engaged only after T is a candidate',
                 'plant-leak' in bounded['candidates'])
    checks.check('a post first engaged exactly at T is not a candidate:'
                 ' "at or before T" includes T',
                 'plant-at-t' not in bounded['candidates'])
    checks.check('a favourite made after T labels the post',
                 'plant-leak' in bounded['labels'])
    checks.check('a favourite first seen exactly at T is not a label:'
                 ' the window opens after T',
                 'plant-at-t' not in bounded['earlier_labels'])
    checks.check('a boost after T labels the post it boosted',
                 'plant-boost-target' in bounded['labels'])
    checks.check('a reply at exactly T does not: the window opens after it',
                 'plant-reply-target' not in bounded['labels'])
    checks.equal('two labels fall within the hour that blurs a flag: the'
                 ' one liked half an hour after T, and the one liked at'
                 ' exactly T + 1 h, whose bound is inclusive',
                 bounded['near'], 2)
    # The three edges the plan pins, each on the side nothing else
    # covers. The candidate window is [T - 24 h, T), so a post created at
    # exactly T is outside it; the label window is (T, T + 24 h], so a
    # post first seen engaged at exactly T + 24 h is inside it; and the
    # near count is the first hour after T, so a post first seen at
    # exactly T + 1 h is outside that.
    checks.check('a post created at exactly T is not a candidate: the'
                 ' window is [T - 24 h, T)',
                 'plant-created-at-t' not in bounded['candidates'])
    checks.check('a post first seen engaged at exactly T + 24 h is a label:'
                 ' the window is (T, T + 24 h]',
                 'plant-at-day-end' in bounded['labels'])
    checks.check('a post first seen engaged at exactly T + 1 h is a label'
                 ' and inside the hour that blurs a flag: the bound is'
                 ' inclusive',
                 'plant-near-edge' in bounded['labels']
                 and 'plant-near-edge' in bounded['near_labels'])
    checks.check('and one first seen a minute later is a label but outside'
                 ' that hour',
                 'plant-past-near' in bounded['labels']
                 and 'plant-past-near' not in bounded['near_labels'])
    checks.check('an engagement after T does not reach the affinity at T',
                 PLANT_LEAK not in bounded['author'])
    checks.check('nor does one first seen exactly at T',
                 'plant-at-t-author' not in bounded['author'])
    checks.check('the same engagement before T does reach it',
                 PLANT_LEAK in control['author'])
    checks.check('so the leaked author\'s post stays outside the top 20',
                 'plant-leak' not in bounded['top'])
    checks.check("and enters it when the favourite is moved before T",
                 'plant-leak' in control['top'])
    checks.check('which is what a query that read one row too many would do',
                 bounded['top'] != control['top'])
    # The premise the cuts rest on: the leak's engagement is first seen
    # inside the hour after T, so a bound of T + 1 h reads it and a bound
    # of T does not.
    leaked_at = parse_moment(bounded['first'].get('plant-leak'))
    checks.check('the leak is first seen at %s, inside (T, T + 1 h]'
                 % iso_utc(leaked_at) if leaked_at else
                 'the leak was never seen engaged',
                 leaked_at is not None
                 and PLANT_T < leaked_at <= PLANT_T + HOUR)

def check_collect(checks, work):
    """`collect` through the fake: what a run writes, what a second run
    adds, and how a rate limit or a failed request stops it."""
    pin_zone()
    world = synthetic_world()
    root = os.path.join(work, 'collect')
    # The first day's 06:00: by then all 31 of its posts exist, and none
    # of them carries a favourite yet.
    moment = WORLD_START + EVALUATION_HOUR * HOUR

    def run(when, printed, where=None, **client):
        return collect(
            SYNTHETIC_INSTANCE,
            make_client=lambda _instance, _token: FakeClient(
                world, when, **client),
            token='synthetic', now=when, root=where or root, out=printed)

    printed = io.StringIO()
    checks.equal('a run exits 0', run(moment, printed), 0)
    checks.equal(
        'the run line counts the posts stored, the new ones, the requests,'
        ' and whether it is partial',
        printed.getvalue().strip(),
        '%s posts=31 new=31 requests=6 partial=False' % iso_utc(moment))
    checks.equal('the data directory is 0700',
                 stat.S_IMODE(os.stat(
                     data_dir(SYNTHETIC_INSTANCE, root)).st_mode), 0o700)
    checks.equal('and the database is 0600',
                 stat.S_IMODE(os.stat(database_path(
                     SYNTHETIC_INSTANCE, root)).st_mode), 0o600)

    same_second = moment + datetime.timedelta(microseconds=500000)
    printed = io.StringIO()
    checks.equal('a second run in the same second exits 0',
                 run(same_second, printed), 0)
    # Five requests, not six: every account is known now, so the run has
    # no relationship to ask about.
    checks.equal('and finds no new post or account to ask about',
                 printed.getvalue().strip(),
                 '%s posts=31 new=0 requests=5 partial=False'
                 % iso_utc(same_second))
    checks.equal('so there are two runs and one row per post',
                 stored_rows(root, 'SELECT (SELECT COUNT(*) FROM runs),'
                                  ' (SELECT COUNT(*) FROM posts)'), (2, 31))
    checks.equal('and each run keeps its own observations',
                 stored_rows(root, 'SELECT (SELECT COUNT(DISTINCT run_ts)'
                                  ' FROM observations),'
                                  ' (SELECT COUNT(*) FROM observations)'),
                 (2, 62))

    limited = os.path.join(work, 'collect-limited')
    printed = io.StringIO()
    checks.equal('a rate-limited run exits 0',
                 run(moment, printed, where=limited, limited=True), 0)
    checks.equal('it stops before the first request, and says why',
                 printed.getvalue().strip(),
                 '%s posts=0 new=0 requests=0 partial=True'
                 % iso_utc(moment))
    checks.equal('the run row records the rate limit',
                 stored_rows(limited, 'SELECT requests, partial, error'
                                     ' FROM runs'),
                 (0, 1, 'rate limited; retry after 30s'))

    failed = os.path.join(work, 'collect-failed')
    printed = io.StringIO()
    checks.equal('a run whose request fails exits 0',
                 run(moment, printed, where=failed,
                     fail_at='/favourites'), 0)
    checks.equal('and keeps what it gathered before the failure',
                 printed.getvalue().strip(),
                 '%s posts=31 new=31 requests=4 partial=True'
                 % iso_utc(moment))
    checks.equal('the run row names the failure',
                 stored_rows(failed, 'SELECT requests, partial, error'
                                    ' FROM runs'),
                 (4, 1, 'the request failed'))
    checks.equal('and the posts gathered before it are stored',
                 stored_rows(failed, 'SELECT COUNT(*) FROM posts')[0], 31)

    class ListBody(FakeClient):
        """Answers the account endpoint with a one-element list."""

        def _answer(self, path, params):
            if path == '/accounts/verify_credentials':
                return [{'id': VIEWER_ID}]
            return super()._answer(path, params)

    gathered, requests = gather(ListBody(world, WORLD_START))
    checks.equal('the account body is read as the object it is, not a list',
                 gathered['error'], 'verify_credentials returned no account')
    checks.equal('and the run stops on its first request', requests, 1)
