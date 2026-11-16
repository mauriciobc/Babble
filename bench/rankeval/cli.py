# SPDX-License-Identifier: GPL-3.0-or-later
"""The command line: the parser, the bench report and ``main``."""

import argparse
import datetime
import random
import src.catchup.rank as _rank
import src.catchup.rank_model as _rank_model
import sys
import time
from rankeval.collect import collect
from rankeval.commands import (ENGAGEMENTS,
    engage,
    follow,
    inspect,
    read_token_line,
    store_token)
from rankeval.evaluate import aggregate, evaluate
from rankeval.selfcheck_world import self_test



# The clock the bench's posts are aged against, so two machines rank the
# same ages and not the same wall-clock offsets.
BENCH_NOW = datetime.datetime(2026, 10, 21, 12, tzinfo=datetime.timezone.utc)

BENCH_AUTHORS = 150

BENCH_RUNS = 3

BENCH_WORDS = ('alpha bravo charlie delta echo foxtrot golf hotel india'
               ' juliet kilo lima mike november oscar papa quebec romeo'
               ' sierra tango uniform victor whiskey xray yankee zulu'
               ' babble timeline ranked affinity timeline feed').split()

def bench_posts(count, seed=20261021):
    """*count* synthetic home posts, as Docs/timeline-algorithm.md §6.1
    describes them: random text of 8 to 60 words, one tag, one link, 150
    authors, 48 hours of ages, no media."""
    rng = random.Random(seed)
    statuses = []
    for index in range(count):
        link = 'https://example.test/%d' % index
        text = ' '.join(rng.choice(BENCH_WORDS)
                        for _word in range(rng.randint(8, 60)))
        created = BENCH_NOW - datetime.timedelta(
            seconds=rng.randint(0, 48 * 60 * 60))
        statuses.append({
            'id': 'bench-%d' % index,
            'created_at': created.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'account': {
                'id': 'bench-author-%d' % (index % BENCH_AUTHORS),
                'acct': 'author-%d' % (index % BENCH_AUTHORS),
                'username': 'author-%d' % (index % BENCH_AUTHORS),
                'followers_count': rng.randint(10, 20000),
                'following_count': rng.randint(30, 2000),
                'statuses_count': rng.randint(100, 20000),
                'note': 'a synthetic account',
            },
            'content': '<p>%s <a href="%s">%s</a></p>' % (text, link, link),
            'card': {'url': link},
            'tags': [{'name': 'synth'}],
            'language': 'en',
            'favourites_count': rng.randint(0, 3000),
            'reblogs_count': rng.randint(0, 500),
            'replies_count': rng.randint(0, 100),
        })
    return statuses

def bench(out=None):
    """Time `src.catchup.rank.rank()` on 400 and 800 synthetic posts, three
    runs each, printing every run. §6.1 records the figure this
    reproduces; medians would hide the first-call warm-up it names."""
    out = out or sys.stdout
    context = _rank_model.RankingContext(now=BENCH_NOW)
    for count in (400, 800):
        candidates = [_rank_model.candidate_from_status(status)
                      for status in bench_posts(count)]
        for attempt in range(1, BENCH_RUNS + 1):
            started = time.perf_counter()
            _rank.rank(candidates, context)
            elapsed = (time.perf_counter() - started) * 1000
            print('%d posts, run %d: %.1f ms' % (count, attempt, elapsed),
                  file=out)
    return 0

def build_parser():
    """The command line. `credits` is T4.3's; it parses and answers, so a
    script can name it without a traceback."""
    parser = argparse.ArgumentParser(
        prog='rank_eval.py',
        description='Record and judge a Mastodon home timeline.')
    commands = parser.add_subparsers(dest='command', required=True)

    collect_command = commands.add_parser(
        'collect', help='append one snapshot of the home timeline')
    collect_command.add_argument(
        '--instance', required=True,
        help='the instance, e.g. https://mastodon.social')
    collect_command.add_argument(
        '--account-id',
        help='read the token Babble stored for this account from the'
             ' keyring (host installs only; a Flatpak keeps its secrets'
             ' inside the sandbox)')

    evaluate_command = commands.add_parser(
        'evaluate', help='judge the recorded feed, day by day')
    evaluate_command.add_argument(
        '--instance', required=True,
        help='the instance whose database is judged')
    evaluate_command.add_argument(
        '--gates', help='the frozen gates file (default: the one beside'
                        ' this script)')
    evaluate_command.add_argument(
        '--json', dest='json_path',
        help='write the summary here (numbers, dates and verdicts only)')
    evaluate_command.add_argument(
        '--account-id',
        help='the account whose data to judge; the same one collect wrote'
             ' under. Without it, the account-less directory is read,'
             ' which is where a single-account collection lands.')

    aggregate_command = commands.add_parser(
        'aggregate', help='the H1 record over several summaries')
    aggregate_command.add_argument(
        'summaries', nargs='+', help='summary JSON files, one per account')
    aggregate_command.add_argument(
        '--gates', help='the frozen gates file (default: the one beside'
                        ' this script)')

    token_command = commands.add_parser(
        'token', help='verify an account token and store it in the keyring')
    token_command.add_argument(
        '--instance', required=True,
        help='the instance the account lives on')
    token_command.add_argument(
        '--account-id',
        help='the id the server names; checked against verify_credentials')
    token_command.add_argument(
        '--token',
        help='the token, when it is not piped in on stdin (a shell keeps'
             ' it in its history)')

    follow_command = commands.add_parser(
        'follow', help='follow accounts for one managed account')
    follow_command.add_argument(
        '--instance', required=True, help='the instance to act on')
    follow_command.add_argument(
        '--account-id', help='the keyring account to act as')
    follow_command.add_argument(
        '--from-account',
        help='follow the accounts this account id follows')
    follow_command.add_argument(
        '--account', dest='accounts', action='append', default=[],
        metavar='ID', help='a candidate account id (repeatable)')
    follow_command.add_argument(
        '--limit', type=int, default=0,
        help='stop after this many follows this run (default: no cap)')
    follow_command.add_argument(
        '--pause', type=float, default=1.0,
        help='seconds to wait between follows (default: 1)')

    engage_command = commands.add_parser(
        'engage', help='favourite, reblog, bookmark or reply, as an account')
    engage_command.add_argument(
        '--instance', required=True, help='the instance to act on')
    engage_command.add_argument(
        '--account-id', help='the keyring account to act as')
    engage_command.add_argument(
        '--target', required=True, help='the status id to engage')
    engage_command.add_argument(
        '--kind', required=True, choices=ENGAGEMENTS)
    engage_command.add_argument(
        '--text', help='the reply body (reply only)')
    engage_command.add_argument(
        '--visibility', help='public or private (reblog and reply)')

    inspect_command = commands.add_parser(
        'inspect',
        help='report follow counts and the home timeline size per account')
    inspect_command.add_argument(
        '--instance', required=True, help='the instance the accounts live on')
    inspect_command.add_argument(
        '--account-id', action='append', required=True, metavar='ID',
        help='a keyring account to report on (repeatable)')
    inspect_command.add_argument(
        '--token', help='act as this token; one --account-id only')

    # `credits` belongs to T4.3, which reads the ranked store's counters
    # table; the store itself is T2.1's. Until then it parses and answers.
    commands.add_parser('credits', help='not implemented yet')
    commands.add_parser(
        'self-test',
        help='check the harness against a synthetic world (no network)')
    commands.add_parser(
        'bench',
        help='time src.catchup.rank.rank() on synthetic posts')
    return parser

def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == 'collect':
        return collect(args.instance, account_id=args.account_id)
    if args.command == 'evaluate':
        return evaluate(args.instance, gates_path=args.gates,
                        json_path=args.json_path,
                        account_id=args.account_id)
    if args.command == 'aggregate':
        return aggregate(args.summaries, gates_path=args.gates)
    if args.command == 'token':
        return store_token(args.instance, account_id=args.account_id,
                           token=args.token or read_token_line())
    if args.command == 'follow':
        return follow(args.instance, from_account=args.from_account,
                      accounts=args.accounts, limit=args.limit,
                      pause=args.pause, account_id=args.account_id)
    if args.command == 'engage':
        return engage(args.instance, args.target, args.kind, text=args.text,
                      visibility=args.visibility,
                      account_id=args.account_id)
    if args.command == 'inspect':
        return inspect(args.instance, args.account_id, token=args.token)
    if args.command == 'self-test':
        return self_test()
    if args.command == 'bench':
        return bench()
    print('%s is not implemented yet' % args.command, file=sys.stderr)
    return 2
