# SPDX-License-Identifier: GPL-3.0-or-later

"""One answer to "what does this filter mean for this post", with no
network and no display.

There used to be two. ``timeline.status_filter`` drew the row's "Filtered:"
bar and read neither ``Filter#context`` nor ``Filter#expires_at``;
``catchupdigest.filter_info`` dropped a ``hide`` post outright and read
both. So a rule scoped to another view still collapsed a post in the home
timeline while Catch Up honoured it, and the same post could be hidden in
one and shown in the other.

The shared implementation is ``filterrules``. This checks the rule itself,
and then the thing that actually broke: that both callers agree with it.
Needs no display -- ``timeline`` is imported for its two pure functions,
not for a widget.
"""

import datetime
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src import filterrules  # noqa: E402

failures = 0

BASE = datetime.datetime(2026, 10, 13, 12, 0, tzinfo=datetime.timezone.utc)


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL - %s' % label)
    else:
        print('ok - %s' % label)


def iso(minutes_ago=0):
    return (BASE - datetime.timedelta(minutes=minutes_ago)) \
        .strftime('%Y-%m-%dT%H:%M:%S.000Z')


def rule(action='warn', title='f', contexts=('home',), expires_at=None):
    found = {'filter_action': action, 'title': title}
    if contexts is not None:
        found['context'] = list(contexts)
    if expires_at is not None:
        found['expires_at'] = expires_at
    return found


def status(*rules, reblog=None):
    built = {'id': '1'}
    if rules:
        built['filtered'] = [{'filter': r} for r in rules]
    if reblog is not None:
        built['reblog'] = reblog
    return built


# The rule itself.

check('an unfiltered post has no hit',
      filterrules.resolve(status()) is None)
check('a home rule reads as a warn',
      filterrules.resolve(status(rule())) ==
      {'action': 'warn', 'titles': ['f']})
check('a rule naming no context applies everywhere',
      filterrules.resolve(status(rule(contexts=None)), 'account')
      is not None)
check('a rule for another context does not apply',
      filterrules.resolve(status(rule(contexts=['public'])))
      is None)
check('a rule naming several contexts applies in each',
      all(filterrules.resolve(status(rule(contexts=['home', 'thread'])), c)
          is not None for c in ('home', 'thread')))
check('a missing action counts as a warn',
      filterrules.resolve(status(rule(action=None)) or
                          status(rule(action=''))) is not None)
check('an action nobody documents counts as a warn',
      filterrules.resolve(status(rule(action='mystery')))['action']
      == 'warn')
check('hide wins over warn',
      filterrules.resolve(status(rule('warn'), rule('hide')))['action']
      == 'hide')
check('hide on the boosted post wins too',
      filterrules.resolve(
          status(rule('warn'), reblog=status(rule('hide'))))['action']
      == 'hide')
check('one blur rule blurs',
      filterrules.resolve(status(rule('blur')))['action'] == 'blur')
check('blur and warn together read as warn',
      filterrules.resolve(status(rule('blur'), rule('warn')))['action']
      == 'warn')
check('every rule is named, not just the deciding one',
      filterrules.resolve(status(rule('blur', 'A'), rule('blur', 'B')))
      == {'action': 'blur', 'titles': ['A', 'B']})
check('a rule with no title names it empty',
      filterrules.resolve(status(rule(title='')))['titles'] == [''])

# Expiry, judged only against a clock the caller supplies.

expiring = status(rule(expires_at=iso(10)))
check('an unexpired rule still applies', filterrules.resolve(expiring) is not None)
check('a rule past its expiry stops applying',
      filterrules.resolve(expiring, 'home', BASE) is None)
check('a rule with no expiry never lapses',
      filterrules.resolve(status(rule()), 'home', BASE) is not None)
check('the clock may be given as a string',
      filterrules.resolve(expiring, 'home', BASE.isoformat()) is None)
check('without a clock nothing is treated as expired',
      filterrules.resolve(expiring) is not None)

# The shape a row's filter bar wants.

check('strongest names the deciding rule',
      filterrules.strongest(status(rule('warn', 'W'))) == ('warn', 'W'))
check('strongest passes the context through',
      filterrules.strongest(status(rule(contexts=['public'])), 'home')
      is None)
check('strongest has nothing to say about a clean post',
      filterrules.strongest(status()) is None)
check('strongest survives a hit with no title',
      filterrules.strongest(status(rule(title=''))) == ('warn', ''))

# parse_time came from catchupdigest, which kept its own copy of it.

check('a Z timestamp parses as UTC',
      filterrules.parse_time(iso()) == BASE)
check('an offset timestamp keeps its offset',
      filterrules.parse_time('2026-10-13T08:00:00-04:00') is not None)
check('a datetime passes straight through',
      filterrules.parse_time(BASE) == BASE)
check('nonsense is None, not an exception',
      filterrules.parse_time('not a time') is None
      and filterrules.parse_time(None) is None
      and filterrules.parse_time('') is None)

# The two callers, which is where the disagreement was.

from src import catchupdigest  # noqa: E402
from src import statusrun, timelinebase, timelinepage  # noqa: E402


class FakeSource:
    def __init__(self, path):
        self.path = path


CASES = [
    status(),
    status(rule()),
    status(rule('hide')),
    status(rule('blur')),
    status(rule('blur'), rule('warn')),
    status(rule('warn'), reblog=status(rule('hide'))),
    status(rule(contexts=['public'])),
    status(rule(contexts=['thread'])),
    status(rule(contexts=None)),
    status(rule(title='')),
    status(rule('hide', 'H'), rule('blur', 'B')),
    status(rule('mystery')),
]

for case in CASES:
    for context in ('home', 'public', 'thread', 'account'):
        shared = filterrules.resolve(case, context)
        check('catch up agrees with filterrules (%r, %s)'
              % (shared and shared['action'], context),
              catchupdigest.filter_info(case, context) == shared)
        check('the timeline agrees with filterrules (%r, %s)'
              % (shared and shared['action'], context),
              timelinebase.status_filter(case, context) == (
                  None if shared is None
                  else (shared['action'],
                        shared['titles'][0] if shared['titles'] else '')))

check('catch up still pins its snapshot clock',
      catchupdigest.parse_time('2026-10-13T12:00:00.000Z') == BASE)

# The page has to say which view it is, or no row can ask.

check('the home timeline is the home context',
      timelinepage.filter_context_for(FakeSource('/timelines/home'))
      == filterrules.HOME)
check('the local timeline is a public view',
      timelinepage.filter_context_for(FakeSource('/timelines/public?local=true'))
      == filterrules.PUBLIC)
check('the federated timeline is a public view',
      timelinepage.filter_context_for(FakeSource('/timelines/public'))
      == filterrules.PUBLIC)
check('a hashtag timeline is a public view',
      timelinepage.filter_context_for(FakeSource('/timelines/tag/rust'))
      == filterrules.PUBLIC)
check('a trending link timeline is a public view',
      timelinepage.filter_context_for(FakeSource('/timelines/link?url=x'))
      == filterrules.PUBLIC)
check('a list timeline is read as home',
      timelinepage.filter_context_for(FakeSource('/timelines/list/3'))
      == filterrules.HOME)
check('an account timeline is the account view',
      timelinepage.filter_context_for(FakeSource('/accounts/42/statuses'))
      == filterrules.ACCOUNT)
check('bookmarks have no context of their own and read as home',
      timelinepage.filter_context_for(FakeSource('/bookmarks'))
      == filterrules.HOME)
check('a source without a path does not crash',
      timelinepage.filter_context_for(object()) == filterrules.HOME)

# And the rows those pages build have to carry it.

check('a fresh shell defaults to the home context',
      statusrun.StatusRunRow.filter_context == filterrules.HOME)

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('every filter rule reads the same in both views')
