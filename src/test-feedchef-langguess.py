#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef: guessing the language of a post that declares none.

Display-free. The maintainer's decision (2026-11-23, ADR deviation 15): a
guess by py3langid is acted on only for a post with no declared language, at
least 40 characters of visible text (links and handles removed as langbench
removes them), a probability of at least 0.63, and only 'en' (VADER) or
'pt' (LeIA). Held here: the gates, the closed set of languages, that a
declared language is never touched, that the guess runs on the engine worker,
that a guessed post replays, and the contract predicate. Skips the checks
that need py3langid (exit 77) when it is missing.
"""

import os
import random
import sys
import tempfile
import threading
import time

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(srcdir))
sys.path.insert(0, os.path.join(os.path.dirname(srcdir), 'bench'))

from feedchefaudit import fuzz  # noqa: E402
from feedchefaudit import langbench  # noqa: E402
from feedchefaudit import replay as replaymod  # noqa: E402
from feedchefaudit.records import expand, load  # noqa: E402
from src.feedchef import contract, deps, langguess  # noqa: E402
from src.feedchef import decisionlog as logmod  # noqa: E402
from src.feedchef import network_reach as reachmod  # noqa: E402
from src.feedchef import sentiment  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402
from src.feedchef.recipe import Modifier, default_recipe  # noqa: E402

failures = 0
TMP = tempfile.mkdtemp(prefix='feedchef-langguess-')
reachmod.set_root(os.path.join(TMP, 'reach'))
logmod.set_root(os.path.join(TMP, 'logs'))
HAVE = not deps.unavailable_reason('py3langid')


def check(label, condition):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)


EN = ('I really love this wonderful sunny morning, everything feels great '
      'and I am so happy about it')
NEG = ('This is the worst terrible awful day, I hate everything about '
       'this horrible situation right now')
PT = ('Estou muito feliz hoje, o dia está maravilhoso e eu adorei passear '
      'com os meus amigos na praia')
ES = ('Hola amigos, hoy es un día precioso y me encanta pasear con mis '
      'amigos por la playa')


def post(text, language=None, sid='1', **extra):
    body = {'id': sid, 'content': '<p>%s</p>' % text, 'spoiler_text': '',
            'created_at': fuzz.NOW.isoformat(), 'sensitive': False,
            'favourites_count': 0, 'reblogs_count': 0, 'replies_count': 0,
            'account': {'id': 'a', 'url': 'https://ex.ample/@a'}, 'tags': [],
            'media_attachments': []}
    if language is not None:
        body['language'] = language
    body.update(extra)
    return body


# --- the constants ------------------------------------------------------------

check('the languages are langbench\'s RUNTIME_LANGS, which measured them',
      langguess.RUNTIME_LANGS == langbench.RUNTIME_LANGS)
check('the gates are the maintainer\'s: 0.63, 40 characters, en and pt',
      langguess.MIN_P == 0.63 and langguess.MIN_CHARS == 40
      and langguess.ACTED == ('en', 'pt'))
check('the text is cleaned exactly as langbench cleans it',
      all(langguess.clean(t) == langbench.clean(t) for t in (
          'see https://a.example/x?y=1 and www.b.example @user@host.example '
          'hi @bob', 'plain  text\n here', EN)))
check('links and handles do not count toward the 40 characters',
      len(langguess.visible_text(post(
          'hi https://example.com/a/very/long/path/that/pads/the/text '
          '@someone@far.away.example'))) < 40)
check('acted(): only en and pt, only at 0.63 or more',
      langguess.acted(('en', 0.63)) == 'en'
      and langguess.acted(('pt', 0.99)) == 'pt'
      and langguess.acted(('en', 0.6299999)) is None
      and langguess.acted(('es', 0.99)) is None
      and langguess.acted(None) is None)

# --- the engine worker (first, before anything guesses on the main thread) ----

if HAVE:
    seen = {}
    real = langguess._classify

    def spy(text):
        seen['thread'] = threading.current_thread()
        return real(text)

    langguess._classify = spy
    guessed = default_recipe()
    guessed.kitchen.modifiers = []
    guessed.diet.hard_filters.positive_only = True
    engine = FeedEngine(
        account_key='g#1', self_account_id='me',
        fetch_scope=lambda m, c: [post(EN, sid='1'), post(NEG, sid='2')],
        search_statuses=lambda q: [])
    done, box = threading.Event(), []
    engine.process_feed(guessed, on_done=lambda r: (box.append(r), done.set()),
                        on_error=lambda e: (box.append(e), done.set()))
    done.wait(60)
    engine.shutdown()
    langguess._classify = real
    worker = seen.get('thread')
    check('the guess ran on the engine worker, never the main thread (%s)'
          % (worker.name if worker else None),
          worker is not None and worker is not threading.main_thread()
          and worker.name.startswith('feedchef-engine')
          and 'py3langid' not in deps._warned)
    check('and Only Positive Posts hid the negative undeclared post',
          box and not isinstance(box[0], BaseException)
          and [i['id'] for i in box[0].items] == ['1'])

# --- unavailable: said, and nothing is judged -------------------------------------

saved = (deps._modules.get('py3langid', 'unset'),
         deps._reasons.get('py3langid', 'unset'))
deps._modules['py3langid'] = None
deps._reasons['py3langid'] = 'ImportError: simulated'
langguess._memo.clear()
check('without py3langid: status says so, nothing is guessed or judged',
      langguess.status() == ('unavailable', 'ImportError: simulated')
      and langguess.guess(post(EN)) is None
      and sentiment.score_status(post(EN)) is None)
if saved[0] == 'unset':
    del deps._modules['py3langid']
else:
    deps._modules['py3langid'] = saved[0]
if saved[1] == 'unset':
    del deps._reasons['py3langid']
else:
    deps._reasons['py3langid'] = saved[1]

# --- a recipe that reads no sentiment never loads py3langid (Principle 6) -----

if HAVE:
    import subprocess
    script = '''
import os, sys
os.environ['GSETTINGS_BACKEND'] = 'memory'
sys.path[:0] = [%r, %r]
from feedchefaudit import fuzz
from src.feedchef import decisionlog, network_reach
from src.feedchef.engine import FeedEngine
from src.feedchef.recipe import default_recipe
import tempfile
tmp = tempfile.mkdtemp()
decisionlog.set_root(tmp + '/l'); network_reach.set_root(tmp + '/r')
text = %r
posts = [{'id': str(i), 'content': '<p>%%s %%d</p>' %% (text, i),
          'created_at': fuzz.NOW.isoformat(), 'spoiler_text': '',
          'account': {'id': 'a%%d' %% i}, 'tags': [],
          'media_attachments': []} for i in range(5)]
recipe = default_recipe(); recipe.kitchen.modifiers = []
engine = FeedEngine(account_key='s#1', self_account_id='me',
                    fetch_scope=lambda m, c: posts, search_statuses=None)
result = engine.process_feed_sync(recipe, count=10)
engine.shutdown()
print('PY3LANGID' if 'py3langid' in sys.modules else 'CLEAN',
      len(result.items), 'lang_guess' in str(result.logger.record['posts']))
''' % (os.path.dirname(srcdir), os.path.join(os.path.dirname(srcdir), 'bench'), EN)
    done_ = subprocess.run([sys.executable, '-I', '-c', script],
                           capture_output=True, text=True, timeout=120)
    check('a recipe without sentiment cooks undeclared posts without '
          'importing py3langid or recording a guess (%s)'
          % done_.stdout.strip(), done_.stdout.split() == [
              'CLEAN', '5', 'False'])

# --- the gates --------------------------------------------------------------------

if HAVE:
    en, pt = langguess.guess(post(EN)), langguess.guess(post(PT))
    check('English and Portuguese text with no language are guessed, sure '
          'enough (%s, %s)' % (en, pt),
          en[0] == 'en' and en[1] >= 0.63 and pt[0] == 'pt'
          and pt[1] >= 0.63 and isinstance(en[1], float))
    check('the guess is deterministic',
          all(langguess.guess(post(EN)) == en for _i in range(20)))
    check('an undeclared English post is judged by VADER, a Portuguese one '
          'by LeIA',
          sentiment.score_status(post(EN)) > 0
          and sentiment.score_status(post(PT)) > 0
          and sentiment.score_status(post(NEG)) < 0
          and sentiment.effective_language(post(EN)) == 'en'
          and sentiment.effective_language(post(PT)) == 'pt')
    check('short text: not guessed, not judged (under 40 characters)',
          langguess.guess(post('I love this wonderful day!')) is None
          and sentiment.score_status(post('I love this wonderful day!'))
          is None)
    exactly = 'I love sunny mornings with great coffee'
    check('the length gate is at 40 characters (%d is out, %d is in)'
          % (len(exactly), len(exactly) + 1),
          len(exactly) == 39 and langguess.guess(post(exactly)) is None
          and langguess.guess(post(exactly + 's')) is not None)
    check('another language: guessed, but not acted on, so not judged',
          langguess.guess(post(ES))[0] == 'es'
          and sentiment.score_status(post(ES)) is None)
    real = langguess._classify
    langguess._classify = lambda text: ('en', 0.629999)
    check('below 0.63 the guess is not acted on',
          sentiment.score_status(post(EN)) is None)
    langguess._classify = lambda text: ('en', 0.63)
    check('at 0.63 it is', sentiment.score_status(post(EN)) > 0)
    langguess._classify = lambda text: (_ for _ in ()).throw(
        AssertionError('a declared language was second-guessed'))
    check('a declared language is never guessed at: English, Portuguese, '
          'and another one (the post is not judged, as before)',
          sentiment.score_status(post(EN, 'en')) > 0
          and sentiment.score_status(post(PT, 'pt-BR')) > 0
          and sentiment.score_status(post(EN, 'es')) is None
          and sentiment.effective_language(post(EN, 'fr')) == 'fr')
    boost = {'id': 'w', 'created_at': fuzz.NOW.isoformat(), 'content': '',
             'account': {'id': 'b', 'url': 'https://b.example/@b'},
             'reblog': post(EN, 'es', sid='o')}
    check('a boost reads the boosted post\'s declared language, not the '
          'wrapper\'s', sentiment.score_status(boost) is None)
    langguess._classify = real

    # --- the contract predicate --------------------------------------------------

    recipe = default_recipe()
    recipe.kitchen.modifiers = []
    recipe.diet.hard_filters.positive_only = True
    judge = lambda status: 0.5  # noqa: E731
    langguess._classify = lambda text: ('en', 0.5)
    low = contract.violations(recipe, post(EN), sentiment=judge,
                              only={'language_guess'})
    langguess._classify = lambda text: ('en', 0.9)
    high = contract.violations(recipe, post(EN), sentiment=judge,
                               only={'language_guess'})
    short = contract.violations(recipe, post('too short'), sentiment=judge,
                                only={'language_guess'})
    declared = contract.violations(recipe, post(EN, 'es'), sentiment=judge,
                                   only={'language_guess'})
    unjudged = contract.violations(recipe, post(EN),
                                   sentiment=lambda s: None,
                                   only={'language_guess'})
    langguess._classify = lambda text: ('en', 0.5)
    unjudged_low = contract.violations(recipe, post(EN),
                                       sentiment=lambda s: None,
                                       only={'language_guess'})
    langguess._classify = real
    check('contract: the engine\'s judgement is held against the gates both '
          'ways: a post judged below the confidence or the length, and a '
          'post the gates allow that was not judged, break language_guess',
          low == ['language_guess'] and short == ['language_guess']
          and high == [] and declared == []
          and unjudged == ['language_guess'] and unjudged_low == [])
    check('contract: the promise is only active when the recipe reads '
          'sentiment',
          contract.violations(default_recipe(), post(EN),
                              sentiment=judge) == []
          and 'language_guess' in contract.PROMISE_IDS)

    # A judge that reads a post the gates would not let it read is caught.
    real_score = sentiment.score_status
    sentiment.score_status = lambda status: 0.5
    engine = FeedEngine(
        account_key='g#2', self_account_id='me',
        fetch_scope=lambda m, c: [post(ES, sid='1'), post(EN, sid='2')],
        search_statuses=lambda q: [])
    broken = engine.process_feed_sync(guessed, count=10)
    engine.shutdown()
    sentiment.score_status = real_score
    check('guard: a judge that reads a Spanish post with no language is '
          'caught and the post dropped (%s)' % broken.guard['dropped'],
          broken.guard['dropped'] == {'language_guess': 1}
          and [i['id'] for i in broken.items] == ['2'])

    # --- a guessed post records its guess and replays ------------------------------

    logmod.clear_all()
    rec = default_recipe('guess')
    rec.kitchen.modifiers = [Modifier(type='sentiment_boost',
                                      sentiment_boost=0.5)]
    for case in range(3):
        rng = random.Random(100 + case)
        fuzz.cook(fuzz.World(rng, rec), rec, case, pages=1, now=fuzz.NOW)
    loaded = [load(p) for p in expand([os.path.join(TMP, 'logs')])]
    guessed_entries = [e for item in loaded for e in item.record['posts']
                       if e['facts'].get('lang_guess')]
    check('the record holds facts.lang_guess {lang, p} with p a float, and '
          'sent.engine (%d posts)' % len(guessed_entries),
          len(guessed_entries) > 5 and all(
              isinstance(e['facts']['lang_guess']['p'], float)
              and set(e['facts']['lang_guess']) == {'lang', 'p'}
              for e in guessed_entries)
          and any(e['sent'] and e['sent']['engine'] in ('vader', 'leia')
                  and not e['facts']['lang'] for e in guessed_entries)
          and all(item.record['env']['lang_guess']['status'] == 'ok'
                  for item in loaded))
    results = [replaymod.replay(item) for item in loaded]
    check('every record with guessed posts replays identically, with no '
          'drift', all(r.status == 'identical' and not r.drift
                       for r in results))
    # A guesser that now answers otherwise is a drift warning, not a
    # mismatch: the replay feeds the recorded guess back.
    langguess._classify = lambda text: ('en', 0.99)
    again = [replaymod.replay(item) for item in loaded]
    langguess._classify = real
    check('a guesser that changed is a drift warning and the replay is '
          'still identical',
          all(r.status == 'identical' for r in again)
          and any(r.drift for r in again))

    # --- behaviour neutrality: posts that declare a language -----------------------

    def declared_world(rng, recipe):
        world = fuzz.World(rng, recipe)
        world.pool = [s for s in world.pool
                      if (s.get('reblog') or s).get('language')]
        world.tag_pool = {}
        world.focus_pool = []
        return world

    def run(patch):
        langguess._classify = patch
        fuzz._ids[0] = 10 ** 6  # the same ids in every run
        out = []
        try:
            for case in range(10):
                rng = random.Random(300 + case)
                recipe = fuzz.random_recipe(rng)
                recipe.kitchen.modifiers = [
                    m for m in recipe.kitchen.modifiers
                    if m.type == 'sentiment_boost']
                recipe.diet.hard_filters.positive_only = True
                world = declared_world(rng, recipe)
                for result in fuzz.cook(world, recipe, case, pages=1,
                                        audit=False, now=fuzz.NOW):
                    out.append(([i['id'] for i in result.items],
                                sorted(result.score_map.items())))
        finally:
            langguess._classify = real
        return out

    def boom(text):
        raise AssertionError('guessed at a declared-language post')

    check('posts that declare a language cook identically with the guesser '
          'on, off or forbidden (the guesser is never asked)',
          run(real) == run(lambda text: None) == run(boom))

    start = time.perf_counter()
    for i in range(50):
        langguess.guess(post('%s number %d' % (EN, i)))
    check('a guess costs well under 50 ms (%.1f ms each)'
          % ((time.perf_counter() - start) * 20),
          (time.perf_counter() - start) < 2.5)

if not HAVE:
    print('skip - py3langid is not installed: the guess checks are skipped')
    sys.exit(1 if failures else 77)
sys.exit(1 if failures else 0)
