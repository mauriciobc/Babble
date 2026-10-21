# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the For You affinity port (no display, no network).

``src/affinity.py`` is a port of the signal-derivation half of Elk's For
You model, so the cases here are Elk's own ``for-you-signals.test.ts``
(the ``deriveAffinities``, ``decayFactor`` and ``sortDedupTruncate``
blocks), translated: ``target({...})``/``withSignals([...])``/
``recordSignal(signals, kind, target, at)`` become a directly constructed
``affinity.Signal`` with the same values.

``GOLDEN`` holds values computed by the TypeScript itself, at
``mauriciobc/elk@9597db5``. They were produced by a temporary
``tests/unit/__babble_golden.test.ts`` in the Elk checkout that imported
``app/composables/for-you/signals.ts`` and wrote its JSON to a file --
``console.log`` is swallowed in that vitest setup -- run from the Elk
checkout as::

    npx vitest run tests/unit/__babble_golden.test.ts

The numbers were copied here and the temporary file was deleted;
``git -C ../elk status --porcelain`` is empty. Every value is asserted to
1e-9. The sequences pinned are a mixed month of favourites, reblogs,
replies and bookmarks with mutuals; the evidence cap on twelve favourites;
the pruning boundary at 0, 13.9, 14 and 14.1 days; the evidence floor at
5.8 and 5.9 days; a dismissal through
``deriveAffinities`` and ``authorPenaltyIn``; and the ``toSignal`` weight
filter for 1, 0, 0.5 and -1.

The port deliberately drops Elk's ``follow`` and passive kinds (see the
module docstring), so ``KINDS`` is asserted against the first five of
``ENGAGEMENT_KINDS`` rather than the whole list.
"""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src import affinity as af  # noqa: E402
from src import catchuprank as cr  # noqa: E402

# Date.parse('2026-01-01T00:00:00Z') in the TypeScript goldens.
NOW = 1_767_225_600_000
DAY = 86_400_000
HOUR = 3_600_000

GOLDEN = {
    'constants': {
        'engagement_kinds': [
            'favourite', 'reblog', 'reply', 'quote', 'bookmark', 'follow',
            'vote', 'open', 'openLink', 'profileClick', 'photoExpand',
            'videoOpen', 'dwell', 'notDwelled',
        ],
        'engagement_strength': {
            'follow': 5, 'reply': 4, 'quote': 4, 'bookmark': 3, 'reblog': 2,
            'favourite': 1, 'vote': 1, 'open': 0.5, 'openLink': 0.5,
            'profileClick': 0.5, 'photoExpand': 0.25, 'videoOpen': 0.25,
            'dwell': 0.25, 'notDwelled': -0.05,
        },
        'evidence_cap': {
            'follow': 1, 'favourite': 10, 'reblog': 10, 'reply': 10,
            'quote': 10, 'bookmark': 10, 'vote': 10, 'open': 4,
            'openLink': 4, 'profileClick': 4, 'photoExpand': 4,
            'videoOpen': 4, 'dwell': 4, 'notDwelled': 8,
        },
        'mutual_multiplier': 3,
        'not_interested_strength': -4,
        'tag_share': 0.5,
        'language_share': 0.25,
        'booster_share': 0.5,
        'max_tags_per_signal': 5,
        'max_signals_per_kind': 50,
        'dismissal_evidence_cap': 10,
        'min_evidence': 0.75,
        'decay_half_life_ms': 1_209_600_000,
        'max_decay_half_lives': 13,
        'prune_below': 0.05,
    },
    'mixed': {
        'author': {
            'alice': 1.1781260690765962,
            'bob': 6.177339130151915,
            'carol': 7.314081925226852,
            'dave': 2.3421275464671303,
        },
        'booster': {},
        'tag': {
            'art': 3.2018850231089457,
            'masto': 1.2842792312665534,
            'vue': 3.657040962613426,
        },
        'language': {
            'de': 0.2379237882526549,
            'en': 2.47066009693999,
            'fr': 1.5443347825379787,
        },
    },
    'evidence_cap': {
        'author': {'a': 10},
        'booster': {},
        'tag': {},
        'language': {},
        'favourites': 12,
    },
    'pruning': {
        '0': {'author': {'a': 1}, 'booster': {}, 'tag': {}, 'language': {}},
        '13.9': {'author': {}, 'booster': {}, 'tag': {}, 'language': {}},
        '14': {'author': {}, 'booster': {}, 'tag': {}, 'language': {}},
        '14.1': {'author': {}, 'booster': {}, 'tag': {}, 'language': {}},
    },
    # The evidence floor, which the ages above do not straddle: a single
    # favourite is worth ``decay(age)`` and is pruned when that falls
    # below MIN_EVIDENCE (0.75), which is at -log2(0.75) * 14 days, about
    # 5.81 days. 5.8 keeps it, 5.9 prunes it.
    'floor': {
        '5.8': {'author': {'a': 0.7503909250825057}, 'booster': {},
                'tag': {}, 'language': {}},
        '5.9': {'author': {}, 'booster': {}, 'tag': {}, 'language': {}},
    },
    'dismissal': {
        'author': {'a': -4},
        'booster': {},
        'tag': {'spam': -2},
        'language': {},
        'penalties': {'one': 0.5, 'other_author': 0, 'missing_author': 0},
    },
    'penalties': {
        '1@0': 0.5,
        '1@14': 0.2928932188134524,
        '2@0': 0.75,
        '2@14': 0.5,
        '3@0': 0.875,
        '3@14': 0.6464466094067263,
        'muted': 1,
    },
    'dismissal_cap': {
        'author': {'x': -40},
        'booster': {},
        'tag': {},
        'language': {},
    },
    'weights': {
        'stored': [
            {'statusId': 'w3', 'weight': None},
            {'statusId': 'w2', 'weight': 0.5},
            {'statusId': 'w1', 'weight': 0},
            {'statusId': 'w0', 'weight': None},
        ],
        'author': {'a3': 1, 'a0': 1},
        'booster': {},
        'tag': {},
        'language': {},
    },
    'prune_below': {
        'author': {'kept': -0.08},
        'booster': {},
        'tag': {},
        'language': {},
    },
    'decay_factor': {
        'negative_day': 1,
        'zero': 1,
        'half_life': 0.5,
        'three_half_lives': 0.125,
        'before_horizon': 0.00012207031256995102,
        'horizon': 0,
        'after_horizon': 0,
    },
    'halving': {
        'after_one_half_life': {
            'author': {'a': 2},
            'booster': {},
            'tag': {'vue': 1},
            'language': {'en': 0.5},
        },
        'after_two': {
            'author': {'a': 1},
            'booster': {},
            'tag': {'vue': 0.5},
            'language': {'en': 0.25},
        },
    },
    'purity': {'once': 2.5, 'stepwise': 2.5},
    'overtake': {
        'author': {'fresh': 4},
        'booster': {},
        'tag': {},
        'language': {},
    },
    'sort_dedup': [
        {'statusId': 'a', 'at': 500},
        {'statusId': 'c', 'at': 300},
        {'statusId': 'b', 'at': 200},
    ],
}

failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL - %s' % label)
    else:
        print('ok - %s' % label)


def close(actual, expected):
    return abs(actual - expected) <= 1e-9


def same_map(actual, expected):
    return (set(actual) == set(expected)
            and all(close(actual[key], value)
                    for key, value in expected.items()))


def check_maps(label, derived, expected):
    for name in ('author', 'booster', 'tag', 'language'):
        check('%s: %s' % (label, name),
              same_map(getattr(derived, name), expected[name]))


def sig(status_id, author_id=None, at_ms=NOW, tags=(), language=None,
        mutual=False, weight=None, booster_id=None):
    """``EngagementSignal``/``target`` with the values Elk's cases use."""
    return af.Signal(status_id=status_id, author_id=author_id,
                     booster_id=booster_id, tags=tuple(tags),
                     language=language, at_ms=at_ms, mutual=mutual,
                     weight=weight)


def derive(entries, dismissed=(), muted=(), now=NOW):
    engaged = {kind: [] for kind in af.KINDS}
    engaged.update(entries)
    return af.derive_affinities(engaged, list(dismissed), muted, now)


# --- Constants, against Elk's own values --------------------------------

constants = GOLDEN['constants']

check("KINDS is the first five of Elk's ENGAGEMENT_KINDS",
      list(af.KINDS) == constants['engagement_kinds'][:5])
check('every ported kind is an Elk engagement kind',
      set(af.KINDS) <= set(constants['engagement_kinds']))
check('ENGAGEMENT_STRENGTH covers exactly KINDS',
      set(af.ENGAGEMENT_STRENGTH) == set(af.KINDS))
check('EVIDENCE_CAP covers exactly KINDS',
      set(af.EVIDENCE_CAP) == set(af.KINDS))
for kind in af.KINDS:
    check('ENGAGEMENT_STRENGTH[%r]' % kind,
          af.ENGAGEMENT_STRENGTH[kind]
          == constants['engagement_strength'][kind])
    check('EVIDENCE_CAP[%r]' % kind,
          af.EVIDENCE_CAP[kind] == constants['evidence_cap'][kind])

check('MUTUAL_MULTIPLIER', af.MUTUAL_MULTIPLIER
      == constants['mutual_multiplier'])
check('NOT_INTERESTED_STRENGTH', af.NOT_INTERESTED_STRENGTH
      == constants['not_interested_strength'])
check('TAG_SHARE', af.TAG_SHARE == constants['tag_share'])
check('LANGUAGE_SHARE', af.LANGUAGE_SHARE == constants['language_share'])
check('BOOSTER_SHARE', af.BOOSTER_SHARE == constants['booster_share'])
check('MAX_TAGS_PER_SIGNAL', af.MAX_TAGS_PER_SIGNAL
      == constants['max_tags_per_signal'])
check('MAX_SIGNALS_PER_KIND', af.MAX_SIGNALS_PER_KIND
      == constants['max_signals_per_kind'])
check('DISMISSAL_EVIDENCE_CAP', af.DISMISSAL_EVIDENCE_CAP
      == constants['dismissal_evidence_cap'])
check('MIN_EVIDENCE', af.MIN_EVIDENCE == constants['min_evidence'])
check('DECAY_HALF_LIFE_MS', af.DECAY_HALF_LIFE_MS
      == constants['decay_half_life_ms'])
check('MAX_DECAY_HALF_LIVES', af.MAX_DECAY_HALF_LIVES
      == constants['max_decay_half_lives'])
check('PRUNE_BELOW', af.PRUNE_BELOW == constants['prune_below'])

check('home-mixer ordering: reply = quote > bookmark > reblog > favourite',
      af.ENGAGEMENT_STRENGTH['reply']
      == af.ENGAGEMENT_STRENGTH['quote']
      > af.ENGAGEMENT_STRENGTH['bookmark']
      > af.ENGAGEMENT_STRENGTH['reblog']
      > af.ENGAGEMENT_STRENGTH['favourite'])
check('a dismissal is four favourites',
      af.NOT_INTERESTED_STRENGTH == -4 * af.ENGAGEMENT_STRENGTH['favourite'])
check('a reply is not ten favourites (the count-equivalence fallacy)',
      af.ENGAGEMENT_STRENGTH['reply']
      / af.ENGAGEMENT_STRENGTH['favourite'] != 10)


# --- sortDedupTruncate --------------------------------------------------

out = af.sort_dedup_truncate([
    sig('a', at_ms=100),
    sig('c', at_ms=300),
    sig('a', at_ms=500),
    sig('b', at_ms=200),
], 10)
check('sortDedupTruncate: newest first, deduped on status id',
      [s.status_id for s in out] == ['a', 'c', 'b'])
check('sortDedupTruncate: the surviving duplicate is the newest',
      out[0].at_ms == 500)
check('sortDedupTruncate: truncates',
      [s.status_id
       for s in af.sort_dedup_truncate(out, 2)] == ['a', 'c'])
check('sortDedupTruncate matches the TypeScript exactly',
      [(s.status_id, s.at_ms) for s in out]
      == [(entry['statusId'], entry['at'])
          for entry in GOLDEN['sort_dedup']])

many = [sig('s%d' % i, 'a%d' % i, at_ms=NOW + i) for i in range(70)]
capped = af.sort_dedup_truncate(many, af.MAX_SIGNALS_PER_KIND)
check('keeps at most MAX_SIGNALS_PER_KIND entries',
      len(capped) == af.MAX_SIGNALS_PER_KIND)
check('drops the oldest when the cap is reached',
      capped[0].status_id == 's69'
      and all(s.status_id != 's0' for s in capped))

refreshed = af.sort_dedup_truncate([
    sig('s1', 'a', at_ms=NOW - DAY),
    sig('s1', 'a', at_ms=NOW),
], 10)
check('re-engaging refreshes the timestamp instead of double counting',
      len(refreshed) == 1 and refreshed[0].at_ms == NOW)
check('re-engaging yields one favourite of affinity',
      close(derive({'favourite': refreshed}).author['a'],
            af.ENGAGEMENT_STRENGTH['favourite']))

undone = [s for s in refreshed if s.status_id != 's1']
check('an undone engagement leaves no affinity behind',
      derive({'favourite': undone}).author == {})


# --- deriveAffinities: strengths, shares, attribution --------------------

shares = derive({'favourite': [sig(
    's1', 'a', tags=('vue', 'nuxt'), language='en')]})
check('the author gets full strength', close(shares.author['a'], 1))
check('a tag gets half', close(shares.tag['vue'], 0.5)
      and close(shares.tag['nuxt'], 0.5))
check('the language gets a quarter', close(shares.language['en'], 0.25))

per_action = derive({
    'favourite': [sig('s1', 'liked')],
    'reply': [sig('s2', 'replied')],
    'quote': [sig('s3', 'quoted')],
    'reblog': [sig('s4', 'boosted')],
    'bookmark': [sig('s5', 'saved')],
})
check('each action applies at its own strength', same_map(per_action.author, {
    'liked': af.ENGAGEMENT_STRENGTH['favourite'],
    'replied': af.ENGAGEMENT_STRENGTH['reply'],
    'quoted': af.ENGAGEMENT_STRENGTH['quote'],
    'boosted': af.ENGAGEMENT_STRENGTH['reblog'],
    'saved': af.ENGAGEMENT_STRENGTH['bookmark'],
}))
check('two posts by the same author add up',
      close(derive({'favourite': [
          sig('s8', 'liked'), sig('s9', 'liked')]}).author['liked'], 2))

stranger = derive({'reply': [sig('s1', 'a')]})
mutual = derive({'reply': [sig('s1', 'a', mutual=True)]})
check('a mutual exchange is tripled',
      close(mutual.author['a'],
            stranger.author['a'] * af.MUTUAL_MULTIPLIER))

boost_status = {
    'id': 'outer',
    'account': {'id': 'booster'},
    'reblog': {
        'id': 'inner',
        'account': {'id': 'original'},
        'tags': [{'name': 'art'}],
        'language': 'fr',
    },
}
boost = af.signal_from_status(boost_status, NOW)
check('a boost targets the original post',
      boost.status_id == 'inner' and boost.author_id == 'original'
      and boost.booster_id == 'booster'
      and boost.tags == ('art',) and boost.language == 'fr')
boosted = derive({'favourite': [boost]})
check('a boost credits the original author',
      close(boosted.author['original'], 1)
      and 'booster' not in boosted.author)
check('the booster earns its own map at half strength',
      close(boosted.booster['booster'], 0.5))
check('the boosted post contributes its tag at half strength',
      close(boosted.tag['art'], 0.5))

muted = derive({'reply': [sig('s1', 'a'), sig('s2', 'a')]}, muted=('a',))
check('a muted author never accrues affinity', muted.author == {})
# Elk hides only the author channel: the topic a muted author's post was
# about is still evidence about the viewer, so tags and language count.
muted_topics = derive(
    {'reply': [sig('s1', 'a', tags=('vue',), language='en')]},
    muted=('a',))
check('a muted author\'s tags and language still count',
      close(muted_topics.tag['vue'], 2)
      and close(muted_topics.language['en'], 1))

status_with_tags = {
    'id': 't1',
    'account': {'id': 'a'},
    'tags': [{'name': name} for name in
             ('One', 'two', 'ONE', 'three', 'four', 'five', 'six')],
}
tagged = af.signal_from_status(status_with_tags, NOW)
# Both slices happen before the dedupe, so a duplicate inside the first
# five keeps a sixth tag out -- exactly what getEngagementTarget + toSignal
# do in the TypeScript.
check('tags are lower-cased and deduplicated in order',
      tagged.tags == ('one', 'two', 'three', 'four'))

distinct = af.signal_from_status({
    'id': 't2', 'account': {'id': 'a'},
    'tags': [{'name': 't%d' % i} for i in range(7)],
}, NOW)
check('at most MAX_TAGS_PER_SIGNAL tags are kept',
      distinct.tags == ('t0', 't1', 't2', 't3', 't4'))


# --- Golden sequences from the TypeScript --------------------------------

# A mixed month, with mutuals.
mixed = derive({
    'favourite': [
        sig('s5', 'alice', at_ms=NOW - DAY, language='de'),
        sig('s1', 'alice', at_ms=NOW - 30 * DAY,
            tags=('art', 'masto'), language='en'),
    ],
    'reblog': [
        sig('s6', 'bob', at_ms=NOW - 2 * DAY, tags=('art',),
            language='fr', mutual=True),
        sig('s2', 'bob', at_ms=NOW - 20 * DAY, tags=('art',),
            language='fr'),
    ],
    'reply': [
        sig('s3', 'carol', at_ms=NOW - 10 * DAY, tags=('vue',),
            language='en', mutual=True),
    ],
    'bookmark': [
        sig('s4', 'dave', at_ms=NOW - 5 * DAY, tags=('masto',),
            language='en'),
    ],
})
check_maps('mixed month with mutuals', mixed, GOLDEN['mixed'])

# The evidence cap: twelve favourites of one author, ten speak.
twelve = [sig('cap%d' % i, 'a') for i in range(12)]
capped_derived = derive({'favourite': twelve})
check_maps('evidence cap', capped_derived, GOLDEN['evidence_cap'])
check('deriving does not truncate the sequence',
      len(twelve) == GOLDEN['evidence_cap']['favourites'])

# The pruning boundary: a single favourite at 0, 13.9, 14, 14.1 days.
for label, age in (('0', 0), ('13.9', 13.9 * DAY),
                   ('14', 14 * DAY), ('14.1', 14.1 * DAY)):
    at = NOW - age
    check_maps('a favourite at %s days' % label,
               derive({'favourite': [sig('p-%s' % label, 'a', at_ms=at)]}),
               GOLDEN['pruning'][label])

# The evidence floor itself, which those ages do not straddle: a single
# favourite survives at 5.8 days and is pruned at 5.9.
for label, age in (('5.8', 5.8 * DAY), ('5.9', 5.9 * DAY)):
    at = NOW - age
    check_maps('a favourite at %s days, at the evidence floor' % label,
               derive({'favourite': [sig('f-%s' % label, 'a', at_ms=at)]}),
               GOLDEN['floor'][label])

# A dismissal, through deriveAffinities.
dismissals = [sig('s1', 'a', tags=('spam',))]
dismissal = derive({}, dismissed=dismissals)
check_maps('a dismissal', dismissal, GOLDEN['dismissal'])
check('one dismissal penalises its author halfway',
      close(af.author_penalty_in(dismissals, (), 'a', NOW),
            GOLDEN['dismissal']['penalties']['one']))
check('another author is unaffected',
      af.author_penalty_in(dismissals, (), 'b', NOW)
      == GOLDEN['dismissal']['penalties']['other_author'])
check('a missing author has no penalty',
      af.author_penalty_in(dismissals, (), None, NOW)
      == GOLDEN['dismissal']['penalties']['missing_author'])

# The dismissal cap: twelve dismissals, ten evidence slots.
twelve_dismissals = [sig('x%d' % i, 'x') for i in range(12)]
check_maps('dismissal evidence cap',
           derive({}, dismissed=twelve_dismissals),
           GOLDEN['dismissal_cap'])

# authorPenaltyIn: one, two and three dismissals, at 0 and 14 days.
for count in (1, 2, 3):
    for label, age in (('0', 0), ('14', 14 * DAY)):
        signals = [sig('d%d' % i, 'a', at_ms=NOW - age)
                   for i in range(count)]
        check('author_penalty_in: %d dismissal(s) at %s days'
              % (count, label),
              close(af.author_penalty_in(signals, (), 'a', NOW),
                    GOLDEN['penalties']['%d@%s' % (count, label)]))
check('a muted author is an absolute penalty',
      af.author_penalty_in([], ('a',), 'a', NOW)
      == GOLDEN['penalties']['muted'])

# The toSignal weight filter: 1, 0, 0.5 and -1.
weight_signals = []
for index, (value, expected) in enumerate(
        ((1, None), (0, 0.0), (0.5, 0.5), (-1, None))):
    signal = af.signal_from_status({
        'id': 'w%d' % index,
        'account': {'id': 'a%d' % index},
        'tags': [],
        'weight': value,
    }, NOW)
    check('toSignal weight %s becomes %r' % (value, expected),
          signal.weight == expected)
    weight_signals.append(signal)
check('the stored weights match the TypeScript',
      {s.status_id: s.weight for s in weight_signals}
      == {entry['statusId']: entry['weight']
          for entry in GOLDEN['weights']['stored']})
check_maps('weighted signals', derive({'favourite': weight_signals}),
           GOLDEN['weights'])

# PRUNE_BELOW: -0.04 is noise, -0.08 survives.
check_maps('a negative entry below PRUNE_BELOW is dropped',
           derive({}, dismissed=[
               sig('b1', 'tiny', weight=0.01),
               sig('b2', 'kept', weight=0.02)]),
           GOLDEN['prune_below'])


# --- decayFactor ---------------------------------------------------------

golden_decay = GOLDEN['decay_factor']
check('decayFactor: a future stamp counts as brand new',
      af.decay_factor(-DAY) == golden_decay['negative_day'])
check('decayFactor: age 0 is brand new',
      af.decay_factor(0) == golden_decay['zero'])
check('decayFactor: one half-life halves',
      close(af.decay_factor(af.DECAY_HALF_LIFE_MS),
            golden_decay['half_life']))
check('decayFactor: three half-lives are one eighth',
      close(af.decay_factor(3 * af.DECAY_HALF_LIFE_MS),
            golden_decay['three_half_lives']))
check('decayFactor: just inside the horizon is tiny but positive',
      close(af.decay_factor(13 * af.DECAY_HALF_LIFE_MS - 1),
            golden_decay['before_horizon']))
check('decayFactor: the horizon itself is zero',
      af.decay_factor(13 * af.DECAY_HALF_LIFE_MS)
      == golden_decay['horizon'])
check('decayFactor: past the horizon stays zero',
      af.decay_factor(13 * af.DECAY_HALF_LIFE_MS + 1)
      == golden_decay['after_horizon'])

# Halving every half-life, and the derived maps with it.
halving_source = {'reply': [sig('s1', 'a', tags=('vue',), language='en')]}
check_maps('one half-life of decay',
           derive(halving_source, now=NOW + af.DECAY_HALF_LIFE_MS),
           GOLDEN['halving']['after_one_half_life'])
check_maps('two half-lives of decay',
           derive(halving_source, now=NOW + 2 * af.DECAY_HALF_LIFE_MS),
           GOLDEN['halving']['after_two'])

# Purity: one derivation at four half-lives equals forty stepwise ones.
purity_signals = [sig('q%d' % i, 'a') for i in range(10)]
once = af.derive_affinities(
    {kind: (purity_signals if kind == 'reply' else [])
     for kind in af.KINDS},
    [], (), NOW + 4 * af.DECAY_HALF_LIFE_MS).author['a']
stepwise_engaged = {kind: (purity_signals if kind == 'reply' else [])
                    for kind in af.KINDS}
last = None
for i in range(1, 41):
    last = af.derive_affinities(
        stepwise_engaged, [], (),
        NOW + (i / 10) * af.DECAY_HALF_LIFE_MS)
check('derivation is a pure function of (sequence, now)',
      close(once, GOLDEN['purity']['once'])
      and close(last.author['a'], GOLDEN['purity']['stepwise'])
      and close(once, last.author['a']))

# A fresh interest overtakes a stale one.
overtake_signals = {
    'favourite': [sig('old%d' % i, 'stale') for i in range(6)],
    'reply': [sig('new', 'fresh', at_ms=NOW + 60 * DAY)],
}
check_maps('a fresh reply overtakes six stale favourites',
           derive(overtake_signals, now=NOW + 60 * DAY),
           GOLDEN['overtake'])

# A clock far in the future zeroes the maps; the sequence restores them.
far = derive({'reply': [sig('s1', 'a', tags=('vue',))]},
             now=NOW + 400 * DAY)
check('a clock far ahead zeroes the derived maps',
      far.author == {} and far.tag == {})
check('a correct clock restores them from the sequence',
      close(derive({'reply': [sig('s1', 'a', tags=('vue',))]}).author['a'],
            4)
      and close(derive({'reply': [sig('s1', 'a', tags=('vue',))]}).tag['vue'],
                2))


# --- resolver ------------------------------------------------------------

positive = derive({'favourite': [sig('s1', 'alice')]})
resolver = af.resolver(positive, (), [], (), NOW)
check('resolver returns an AffinityResolver',
      isinstance(resolver, cr.AffinityResolver))
check('the resolver author channel is positive for a positive author',
      resolver.author('alice') > 0)
check('and zero for an unknown author', resolver.author('nobody') == 0)

for count in (1, 2, 3):
    for label, age in (('0', 0), ('14', 14 * DAY)):
        signals = [sig('d%d' % i, 'a', at_ms=NOW - age)
                   for i in range(count)]
        wired = af.resolver(af.Affinities({}, {}, {}, {}), (), signals,
                            (), NOW)
        check('resolver author_penalty matches authorPenaltyIn: '
              '%d dismissal(s) at %s days' % (count, label),
              close(wired.author_penalty('a'),
                    GOLDEN['penalties']['%d@%s' % (count, label)]))
muted_resolver = af.resolver(af.Affinities({}, {}, {}, {}), (), [], ('a',),
                             NOW)
check('resolver author_penalty is 1 for a muted author',
      muted_resolver.author_penalty('a') == 1)
check('and 0 for everyone else', muted_resolver.author_penalty('b') == 0)


if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('all affinity checks passed')
