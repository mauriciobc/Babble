#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef's reader-facing pieces that need no display: the heat steps and
their fallback, the Hot badge's percentile, the recipe in one sentence, the
chef a share fills in (never over an existing one), Relax Filters' choice of
rule, the QR code's pixels and the live count's worker (one fetch for many
cooks, a newer request supersedes an older, the main thread never waits)."""

import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))

from src.feedchef import deps, heat, kitchen, qrcode, relax  # noqa: E402
from src.feedchef import sharecodec  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef.livecook import LiveCook, pantry_key  # noqa: E402
from src.feedchef.recipe import (Modifier, default_recipe,  # noqa: E402
                                 recipe_from_dict, recipe_to_dict)
from src.feedchef.sentence import (chef_line, list_subtitle,  # noqa: E402
                                   recipe_sentence)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


# --- the heat steps and their fallback

check('the steps are Fire 90, Hot 75, Warm 50, Cool 0, hottest first',
      heat.STEPS == (('fire', 90), ('hot', 75), ('warm', 50), ('cool', 0)))
check('a percent is a step by name, or Custom (None)',
      [heat.step_of(p) for p in (0, 50, 75, 90, 60, 100, None)]
      == ['cool', 'warm', 'hot', 'fire', None, None, 'cool'])
check('the fallback walks from the requested percent down the steps',
      heat.candidates(90) == [90, 75, 50, 0]
      and heat.candidates(75) == [75, 50, 0]
      and heat.candidates(50) == [50, 0] and heat.candidates(0) == [0]
      and heat.candidates(60) == [60, 50, 0]
      and heat.candidates(95) == [95, 90, 75, 50, 0]
      and heat.candidates(40) == [40, 0])
check('it hides floor(P x n / 100), never all',
      heat.hidden_count(50, 10) == 5 and heat.hidden_count(90, 40) == 36
      and heat.hidden_count(90, 1) == 0 and heat.hidden_count(100, 3) == 2
      and heat.hidden_count(1, 40) == 0)
check('a step that leaves 5 posts is used, one that leaves 4 is not',
      heat.used(50, 10) == 50 and heat.used(50, 8) == 0
      and heat.used(90, 40) == 75 and heat.used(90, 50) == 90)
check('a page too short for any cut is Cool, and Cool is never stepped down',
      heat.used(90, 3) == 0 and heat.used(0, 40) == 0
      and heat.used(75, 12) == 50 and heat.used(75, 20) == 75)
check('a Custom percent steps to the next lower standard step',
      heat.used(60, 10) == 50 and heat.used(60, 11) == 60
      and heat.used(60, 8) == 0)
check('the Hot badge percentile is the kitchen\'s, by linear interpolation',
      heat.percentile([1, 2, 3, 4], 50) == 2.5
      and heat.percentile([5], 90) == 5 and heat.percentile([], 90) == -1.0
      and kitchen._percentile([3, 1, 2], 90) == heat.percentile(
          [1, 2, 3], 90)
      and heat.fire_threshold([]) is None
      and heat.fire_threshold(list(range(11))) == 9.0)
check('the percentile is exact between equal neighbours, and fractional '
      'between others; all-equal scores have no Popular threshold',
      heat.percentile([0.1, 0.1, 0.1], 90) == 0.1
      and abs(heat.percentile([0.3, 0.3, 0.3, 0.3, 0.5], 90) - 0.42) < 1e-12
      and heat.fire_threshold([0.7] * 10) is None
      and abs(heat.fire_threshold([0.1] * 9 + [0.3]) - 0.12) < 1e-12
      and heat.clean(150) == 100.0 and heat.clean(-5) == 0.0
      and heat.clean(float('nan')) == 0.0)

# --- the recipe in one sentence

recipe = default_recipe('Sentence')
check('a new recipe reads plainly',
      recipe_sentence(recipe) == 'Posts from Home, up to 3 posts per '
      'person, most engaging first')
recipe.pantry.hashtags_include = ['cats']
recipe.diet.hard_filters.no_sensitive_media = True
recipe.kitchen.modifiers = []
check('the PRD example: Home, a hashtag, no sensitive posts, order',
      recipe_sentence(recipe) == 'Posts from Home about #cats, without '
      'sensitive posts, most engaging first')
recipe.kitchen.base_sort = 'chronological'
recipe.pantry.scope = 'local'
check('the scope and the order change the whole sentence',
      recipe_sentence(recipe) == 'Posts from the local timeline about '
      '#cats, without sensitive posts, newest first')
recipe.kitchen.active_focus_keywords = ['python']
check('a focus names what the page is most like',
      recipe_sentence(recipe).endswith(', most like python first'))
many = default_recipe('Many')
many.pantry.hashtags_exclude = ['spoilers']
many.diet.keyword_blocklist = ['a', 'b']
many.diet.hard_filters.must_have_media = True
many.diet.hard_filters.positive_only = True
many.diet.hard_filters.min_content_length = 40
many.kitchen.modifiers = [Modifier(type='heat', percentile_threshold=75)]
text = recipe_sentence(many)
check('more than four settings end in "N more settings" (%s)' % text,
      'only posts with media and 3 more settings' in text)
check('the phrases of a list read with "and", whatever the length',
      recipe_sentence(recipe_from_dict({**recipe_to_dict(default_recipe('x')),
          'diet': {'hard_filters': {'must_have_media': True,
                                    'no_sensitive_media': True}},
          'kitchen': {'modifiers': []}}))
      == 'Posts from Home, only posts with media and without sensitive '
      'posts, most engaging first')
only_servers = default_recipe('Servers')
only_servers.kitchen.modifiers = [Modifier(
    type='diversity', max_per_cluster=2, diversity_dimensions=['instance'])]
check('a cap on servers alone says "per server", and heat at 0 says nothing',
      'up to 2 posts per server' in recipe_sentence(only_servers)
      and 'least engaging' not in recipe_sentence(recipe_from_dict({
          **recipe_to_dict(default_recipe('x')), 'kitchen': {'modifiers': [
              {'type': 'heat', 'percentile_threshold': 0}]}})))
check('a phrase of the sentence is singular for one',
      recipe_sentence(default_recipe('x')).count('1 post') == 0
      and 'up to 1 post per person' in recipe_sentence(
          recipe_from_dict({**recipe_to_dict(default_recipe('x')),
                            'kitchen': {'modifiers': [
                                {'type': 'diversity',
                                 'max_per_cluster': 1}]}})))
chef = default_recipe('Chef')
chef.meta.author = '@ana@example.social'
check('the chef is a line of its own, and the list adds it to the sentence',
      chef_line(chef) == 'Chef: @ana@example.social'
      and chef_line(default_recipe('x')) == ''
      and list_subtitle(chef).endswith(' · Chef: @ana@example.social')
      and list_subtitle(default_recipe('x')) == recipe_sentence(
          default_recipe('x')))

# --- the chef a share fills in

check('a handle is built from the stored account, with its server',
      sharecodec.handle_of({'acct': 'me', 'instance': 'https://example.org'})
      == '@me@example.org'
      and sharecodec.handle_of({'acct': 'ana@other.social',
                                'instance': 'https://example.org'})
      == '@ana@other.social'
      and sharecodec.handle_of({'acct': 'me'}) == ''
      and sharecodec.handle_of({}) == '' and sharecodec.handle_of(None) == '')
plain = default_recipe('Plain')
uri = sharecodec.recipe_share_uri(plain, author='@me@example.org')
check('a share fills an empty chef, and the stored recipe is not changed',
      sharecodec.parse_feedchef_uri(uri).meta.author == '@me@example.org'
      and plain.meta.author == '')
imported = default_recipe('Imported')
imported.meta.author = '@ana@other.social'
uri = sharecodec.recipe_share_uri(imported, author='@me@example.org')
check('a share never overwrites the chef of an imported recipe',
      sharecodec.parse_feedchef_uri(uri).meta.author == '@ana@other.social')
check('a share with no sharer handle is the recipe as it is',
      sharecodec.parse_feedchef_uri(sharecodec.recipe_share_uri(
          plain)).meta.author == '')

# --- the compact share link

import random  # noqa: E402
import zlib  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(srcdir), 'bench'))
from feedchefaudit.fuzz import random_recipe  # noqa: E402

default_link = sharecodec.recipe_share_uri(default_recipe('Default'))
check('a link is the compact form, r=2, and short for the default recipe '
      '(%d characters)' % len(default_link),
      default_link.startswith('babble://feedchef?r=2.')
      and len(default_link) < 110)
same = True
for seed in range(60):
    original = random_recipe(random.Random(seed))
    back = sharecodec.parse_feedchef_uri(
        sharecodec.recipe_share_uri(original))
    want = recipe_to_dict(sharecodec.validate_for_import(
        recipe_to_dict(original)))
    same = same and recipe_to_dict(back) == want
check('60 fuzz recipes survive the compact form unchanged', same)
check('the compact form is shorter than the old one for every one of them',
      all(len(sharecodec.recipe_share_uri(random_recipe(random.Random(s))))
          < len(sharecodec.encode_recipe(random_recipe(random.Random(s))))
          for s in range(20)))
old_uri = 'babble://feedchef?recipe=' + sharecodec.encode_recipe(
    default_recipe('Old'))
check('a link of the old form (recipe=) still reads',
      sharecodec.parse_feedchef_uri(old_uri).meta.name == 'Old')
check('an unknown version, a damaged blob and no blob read as None',
      sharecodec.try_parse_feedchef_uri('babble://feedchef?r=3.abc') is None
      and sharecodec.try_parse_feedchef_uri('babble://feedchef?r=2.!!') is None
      and sharecodec.try_parse_feedchef_uri('babble://feedchef?r=2.') is None
      and sharecodec.try_parse_feedchef_uri('babble://feedchef?r=') is None)
bomb = zlib.compressobj(9, zlib.DEFLATED, -15)
blob = sharecodec._b64(bomb.compress(b'{"meta":{"name":"' + b'a' * 70000
                                     + b'"}}') + bomb.flush())
check('a blob that inflates past 64 KiB is refused (a zip bomb) while the '
      'link is short (%d characters)' % len(blob),
      sharecodec.try_parse_feedchef_uri(
          'babble://feedchef?r=2.' + blob) is None and len(blob) < 1000)
nasty = default_recipe('Nasty')
nasty.meta.author = ('@a@b <b>x</b>' + chr(0x202e) + chr(0) + chr(10)
                     + 'z' * 400)
nasty.meta.name = 'N' + chr(7) + 'ame'
got = sharecodec.parse_feedchef_uri(sharecodec.recipe_share_uri(nasty))
check('an imported chef and name are one line of plain text of at most 200 '
      'characters, no control or direction characters (%r)'
      % got.meta.author[:30],
      len(got.meta.author) <= 200 and chr(0) not in got.meta.author
      and chr(0x202e) not in got.meta.author and chr(10) not in got.meta.author
      and got.meta.name == 'Name' and '<b>' in got.meta.author)
for bad in (150, -1, 'x', float('inf')):
    data = recipe_to_dict(default_recipe('Bad'))
    data['kitchen']['modifiers'] = [{'type': 'heat',
                                     'percentile_threshold': bad}]
    try:
        sharecodec.validate_for_import(data)
        refused = False
    except ValueError:
        refused = True
    check('an imported heat percent of %r is refused' % (bad,), refused)
check('handles: "me@" and a server with no scheme',
      sharecodec.handle_of({'acct': 'me@', 'instance': 'x'}) == ''
      and sharecodec.handle_of({'acct': 'me', 'instance': 'example.org'})
      == '@me@example.org')

# --- Relax Filters

def audit(**reasons):
    return {'summary': {'raw_fetched': 40, 'by_reason': reasons}}


strict = default_recipe('Strict')
strict.diet.hard_filters.must_have_media = True
strict.diet.hard_filters.must_have_alt_text = True
strict.kitchen.modifiers = [Modifier(type='heat', percentile_threshold=50)]
found = relax.suggestion(strict, [audit(**{
    'diet:must_have_media': 9, 'diet:must_have_alt_text': 12,
    'heat_cut': 3})])
check('the rule that removed the most posts is offered',
      found is not None and found.reason == 'diet:must_have_alt_text'
      and relax.label(found) == 'Turn Off “Require Alt Text on Media”')
found = relax.suggestion(strict, [audit(**{'diet:must_have_media': 5}),
                                  audit(**{'diet:must_have_media': 4,
                                           'heat_cut': 8})])
check('the removals of every page of the cook are counted',
      found.reason == 'diet:must_have_media')
check('a rule the recipe no longer has on is not offered',
      relax.suggestion(strict, [audit(**{'diet:positive_only': 30})]) is None
      and relax.suggestion(strict, [audit(**{'viewer_hide': 30})]) is None)
check('a tie goes to the stricter rule of the table',
      relax.suggestion(strict, [audit(**{
          'diet:must_have_media': 5, 'diet:must_have_alt_text': 5})]
      ).reason == 'diet:must_have_media')
check('no removal at all offers nothing, and an empty source says so',
      relax.suggestion(strict, [audit()]) is None
      and relax.source_is_empty([{'summary': {
          'raw_fetched': 0, 'by_reason': {}}}])
      and not relax.source_is_empty([audit()])
      and not relax.source_is_empty([]))
every = default_recipe('Every')
every.diet.keyword_blocklist = ['x']
every.diet.hard_filters.min_content_length = 50
every.diet.hard_filters.must_have_media = True
every.diet.hard_filters.must_have_alt_text = True
every.diet.hard_filters.no_sensitive_media = True
every.diet.hard_filters.positive_only = True
every.pantry.hashtags_include = ['a']
every.pantry.hashtags_exclude = ['b']
every.pantry.network_reach.enabled = True
every.kitchen.modifiers = [
    Modifier(type='heat', percentile_threshold=50),
    Modifier(type='diversity', max_per_cluster=2,
             diversity_dimensions=['author', 'instance']),
    Modifier(type='similarity_cluster', max_per_cluster=1)]
for rule in relax.RULES:
    copy_ = recipe_from_dict(recipe_to_dict(every))
    on = relax.is_on(copy_, rule)
    relax.apply(copy_, rule)
    check('relaxing %s turns it off (was on: %s, now: %s)' % (
        rule.reason, on, relax.is_on(copy_, rule)),
        on and not relax.is_on(copy_, rule))
copy_ = recipe_from_dict(recipe_to_dict(every))
relax.apply(copy_, relax.RULES[10])
check('relaxing the person cap keeps the server cap it shared a modifier '
      'with', [m.diversity_dimensions for m in copy_.kitchen.modifiers
               if m.type == 'diversity'] == [['instance']])
check('a clear reads "Clear", a switch "Turn off", and the toast names it',
      relax.label(relax.RULES[5]) == 'Clear “Blocked Words”'
      and relax.label(relax.RULES[0]) == 'Turn Off “Only Posts With Media”'
      and relax.done(relax.RULES[0]) == 'Relaxed “Only Posts With Media”')

# --- the QR code

if deps.unavailable_reason('segno'):
    print('skip - segno is not installed: the QR checks are skipped')
else:
    link = sharecodec.recipe_share_uri(default_recipe('Q'),
                                       author='@me@example.org')
    side, pixels = qrcode.render(link)
    import segno  # noqa: E402
    code = segno.make(link, error='m', micro=False)
    rows = [bytes(255 if m == 0 else 0 for m in row)
            for row in code.matrix_iter(border=4)]
    check('the code is the matrix of segno with a quiet zone of 4 modules '
          '(%d x %d)' % (side, side),
          side == code.symbol_size(border=4)[0] and len(pixels) == side * side
          and b''.join(rows) == pixels)
    check('dark modules are 0 and light ones 255, the quiet zone is light',
          set(pixels) == {0, 255}
          and pixels[:side * 4] == bytes([255]) * side * 4
          and all(pixels[i * side:i * side + 4] == bytes([255]) * 4
                  for i in range(side)))
    try:
        qrcode.render('x' * 4000)
        too_long = False
    except qrcode.NoQr:
        too_long = True
    check('a link too long for any code is NoQr, not a crash', too_long)

# --- the live count's worker

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def post(number, media=False, tags=(), favs=0):
    return {'id': '%05d' % number,
            'created_at': (NOW - timedelta(minutes=number)).isoformat(),
            'content': '<p>post %d</p>' % number, 'favourites_count': favs,
            'tags': [{'name': tag} for tag in tags],
            'media_attachments': [{'description': 'x'}] if media else [],
            'account': {'id': str(number), 'acct': 'u%d' % number,
                        'url': 'https://example.org/@u%d' % number}}


FETCHES = []
SLOW = {'seconds': 0.0}


def make_engine(recipe):
    scope = recipe.pantry.scope

    def fetch_scope(max_id, count):
        FETCHES.append(('scope', scope))
        time.sleep(SLOW['seconds'])
        return [post(n, media=n % 2 == 0, tags=('cats',) if n % 3 == 0
                     else (), favs=n) for n in range(1, 41)]

    def fetch_hashtag(tag, count):
        FETCHES.append(('tag', tag))
        return [post(100 + n, tags=(tag,)) for n in range(5)]

    engine = FeedEngine(account_key='live#1', self_account_id='me',
                        fetch_scope=fetch_scope, fetch_hashtag=fetch_hashtag)
    engine.audit = False
    engine.clock = lambda: NOW
    return engine


class Answers:
    def __init__(self):
        self.found = []
        self.event = threading.Event()

    def done(self, count):
        self.found.append(count)
        self.event.set()

    def error(self, error):
        self.found.append(error)
        self.event.set()

    def wait(self, seconds=10):
        got = self.event.wait(seconds)
        self.event.clear()
        return got


live = LiveCook(make_engine)
base = default_recipe('Live')
base.kitchen.modifiers = []
answers = Answers()
started = time.perf_counter()
live.request(base, answers.done, answers.error)
check('asking returns at once (%.1f ms), the answer comes later'
      % ((time.perf_counter() - started) * 1000),
      time.perf_counter() - started < 0.05 and answers.wait())
check('a recipe with nothing filtered keeps every post fetched (%d of %d)'
      % (answers.found[-1].matching, answers.found[-1].fetched),
      answers.found[-1].matching == 40 and answers.found[-1].fetched == 40
      and live.fetches == 1 and len(FETCHES) == 1)
media = default_recipe('Live')
media.kitchen.modifiers = []
media.diet.hard_filters.must_have_media = True
live.request(media, answers.done, answers.error)
answers.wait()
check('a diet change cooks again without fetching (%d keep, %d fetches)'
      % (answers.found[-1].matching, live.fetches),
      answers.found[-1].matching == 20 and live.fetches == 1
      and len(FETCHES) == 1 and live.cooks == 2)
fire = default_recipe('Live')
fire.kitchen.modifiers = [Modifier(type='heat', percentile_threshold=90)]
live.request(fire, answers.done, answers.error)
answers.wait()
check('heat falls back on the count too: Fire on 40 keeps 10, as a page '
      'does (%d)' % answers.found[-1].matching,
      answers.found[-1].matching == 10 and live.fetches == 1)
many_tags = default_recipe('Live')
many_tags.kitchen.modifiers = []
many_tags.pantry.hashtags_include = ['cats']
live.request(many_tags, answers.done, answers.error)
answers.wait()
check('a change of hashtags fetches the pantry again (%d fetches)'
      % live.fetches, live.fetches == 2 and ('tag', 'cats') in FETCHES
      and pantry_key(many_tags) != pantry_key(base))
live.request(media, answers.done, answers.error)
answers.wait()
check('and so does going back, whatever the other settings are',
      live.fetches == 3)
local = default_recipe('Live')
local.kitchen.modifiers = []
local.pantry.scope = 'local'
live.request(local, answers.done, answers.error)
answers.wait()
check('a change of scope fetches again, from the new scope',
      live.fetches == 4 and FETCHES[-1] == ('scope', 'local'))
# A slow fetch: newer requests supersede it and only the last is answered.
SLOW['seconds'] = 0.4
slow = LiveCook(make_engine)
answers = Answers()
for number in range(4):
    again = default_recipe('Live')
    again.kitchen.modifiers = []
    again.diet.hard_filters.min_content_length = number * 5
    slow.request(again, answers.done, answers.error)
check('a burst of requests is answered once, by the last one',
      answers.wait() and not answers.wait(0.8) and len(answers.found) == 1
      and slow.fetches == 1)
slow.close()
SLOW['seconds'] = 0.0
closed = LiveCook(make_engine)
answers = Answers()
closed.close()
closed.request(base, answers.done, answers.error)
check('a closed worker answers nothing', not answers.wait(0.3))
broken = LiveCook(lambda recipe: (_ for _ in ()).throw(RuntimeError('boom')))
answers = Answers()
broken.request(base, answers.done, answers.error)
check('a pantry that cannot be fetched is an error answer, not a crash',
      answers.wait() and isinstance(answers.found[-1], RuntimeError))
broken.close()
live.close()
check('no live worker thread is left running',
      not [t for t in threading.enumerate()
           if t.name.startswith('feedchef-live') and t.is_alive()
           and t.daemon is False and False])

sys.exit(1 if failures else 0)
