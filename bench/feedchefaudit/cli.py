# SPDX-License-Identifier: GPL-3.0-or-later
"""The command line of the Feed Chef audit: ``show``, ``replay``,
``evaluate``, ``collect``, ``langbench`` and ``self-test``."""

import argparse
import sys

from feedchefaudit import evaluate as evalmod
from feedchefaudit import replay as replaymod
from feedchefaudit.collect import collect
from feedchefaudit.langbench import langbench
from feedchefaudit.records import LogError, Loaded, expand, load
from feedchefaudit.selfcheck import run_self_test
from feedchefaudit.show import show


def build_parser():
    parser = argparse.ArgumentParser(
        prog='feedchef_audit.py',
        description='Show, replay and evaluate Feed Chef audit logs.')
    commands = parser.add_subparsers(dest='command', required=True)
    shown = commands.add_parser('show', help='read one log')
    shown.add_argument('log')
    shown.add_argument('--post', help='one post, by the end of its id')
    shown.add_argument('--text', action='store_true',
                       help='include the text kept in the sidecar')
    replayed = commands.add_parser(
        'replay', help='cook the recorded page again and compare')
    replayed.add_argument('logs', nargs='+')
    replayed.add_argument('--force', action='store_true',
                          help='replay a record of another engine revision')
    replayed.add_argument(
        '--allow-partial', action='store_true',
        help='accept posts whose text cannot be checked against its hash '
             '(exit 3 otherwise)')
    evaluated = commands.add_parser(
        'evaluate', help='check the choices against the recipe')
    evaluated.add_argument('logs', nargs='*')
    collected = commands.add_parser(
        'collect', help='replay and evaluate a folder of logs')
    collected.add_argument('paths', nargs='*',
                           help='logs or folders (default: the app\'s)')
    collected.add_argument('--recipe', help='only this recipe id')
    collected.add_argument('--since', help='only this day on (YYYY-MM-DD)')
    collected.add_argument('--json', dest='json_path',
                           help='write counts, verdicts and dates only')
    collected.add_argument('--force', action='store_true')
    collected.add_argument('--allow-partial', action='store_true',
                           help='accept posts whose text is unverified')
    lang = commands.add_parser(
        'langbench',
        help='language-ID precision and recall against declared languages')
    lang.add_argument('paths', nargs='*',
                      help='logs or folders (default: the app\'s)')
    lang.add_argument('--simplemma', action='store_true',
                      help='also compare simplemma, when importable')
    commands.add_parser('self-test', help='check the harness itself')
    return parser


def _replay(args):
    code = 0
    for path in expand(args.logs):
        try:
            result = replaymod.replay(load(path), args.force)
        except LogError as error:
            print('%s: cannot replay: %s' % (path, error))
            code = max(code, 2)
            continue
        extra = ' (%d posts on recorded text verdicts)' % result.partial \
            if result.partial else ''
        print('%s: %s%s%s' % (
            path, result.status,
            ': ' + result.detail if result.detail else '', extra))
        for _sid in result.drift:
            print('  warning: a sentiment score or language guess drifted '
                  'from the record')
        if result.unverified:
            print('  text unverified for %d posts: the sidecar is missing '
                  'or is not the one this record was made with'
                  % result.unverified)
            if not args.allow_partial:
                code = max(code, 3)
        if result.status == 'mismatch':
            code = max(code, 1)
    return code


def _evaluate(args):
    loaded, rejects = [], []
    for path in expand(args.logs):
        try:
            loaded.append(load(path))
        except LogError as error:
            rejects.append((Loaded(path, None, None, None), str(error)))
    cooks = evalmod.group_cooks(loaded, rejects)
    if not cooks and not rejects:
        print('no audit logs to evaluate')
        return 2
    tally = evalmod.reject_tally(rejects)
    for pages in cooks.values():
        tally.merge(evalmod.evaluate_cook(pages))
    for rule in evalmod.RULES:
        print('%-16s %-4s  %d checked, %d violations' % (
            rule, tally.verdict(rule), tally.checked[rule],
            tally.violations[rule]))
        for example in tally.examples[rule]:
            print('    %s' % example)
    print('overall: %s' % tally.overall())
    return 0 if tally.ok() else 1


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == 'show':
        return show(args.log, post=args.post, text=args.text)
    if args.command == 'replay':
        return _replay(args)
    if args.command == 'evaluate':
        return _evaluate(args)
    if args.command == 'collect':
        return collect(args.paths, recipe_id=args.recipe, since=args.since,
                       json_path=args.json_path, force=args.force,
                       allow_partial=args.allow_partial)
    if args.command == 'langbench':
        return langbench(args.paths, with_simplemma=args.simplemma)
    if args.command == 'self-test':
        return run_self_test()
    return 2


if __name__ == '__main__':
    sys.exit(main())
