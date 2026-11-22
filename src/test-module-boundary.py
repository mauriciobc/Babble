# SPDX-License-Identifier: GPL-3.0-or-later

"""The Catch Up boundary, as a test so it cannot drift silently.

`Docs/timeline-algorithm.md` §5.0 draws a line through the module graph:
the ranked place may use Catch Up's ranker and nothing else of Catch Up,
Catch Up may not use the ranked place, and the ranker may use only the
standard library. That is an architectural promise rather than a runtime
behaviour, so nothing else in the tree would notice it being broken; an
import added for convenience would fail only in review, or not at all.

The checker reads sources with `ast`, as `ui strings` does, so an import
inside a function counts (`pipeline.compute()` imports its ranker lazily).
It is a function rather than the script body so the self-checks can feed it
synthetic sources; each self-check requires an exact violation.
"""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(srcdir)
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from testkit import moduleboundary as kit  # noqa: E402
from testkit.moduleboundary import (BENCH, check,  # noqa: E402
                                   pages_name_violations, violations)

# Catch Up's own modules are package submodules; the source parser records
# their first module under `src` for each supported import form.

# The harness lives outside src/ and does not exist yet; it is read only
# once it appears.

# The ways to reach the network that the harness must not take for
# itself. The server is reached through `src/mastodon.py`, which owns the
# token and the request shape; a second HTTP path inside the harness
# would be a second way for a bearer token to leave the machine.






















# R10: the page toolkit and the link registry sit below the plugin and
# web-column modules and may not import them anywhere, function bodies
# included; those modules register through `linkhandlers` instead.














def self_check(label, sources, expected):
    """Assert that the checker reports the *expected* violation line."""
    found = violations(sources)
    ok = any(line.startswith(expected) for line in found)
    kit.check(label, ok)
    if not ok:
        for line in found:
            print('    %s' % line)


# --- The checker itself, before it judges the tree ----------------------
#
# Each rule gets synthetic source that violates it, so a broken matcher
# cannot pass merely because the current tree happens to respect the rule.

self_check('R1 catches a new module importing Catch Up',
           {'src/rankedfeed.py': 'from src.catchup import store\n'},
           'R1: src/rankedfeed.py:1 imports store')
self_check('R2 catches a Catch Up module importing a new module',
           {'src/catchup/tiles.py': 'def load():\n    from . import rankedfeed\n'},
           'R2: src/catchup/tiles.py:2 imports rankedfeed')
self_check('R3 catches an import outside the standard library',
           {'src/catchup/rank_model.py': 'import requests\n'},
           'R3: src/catchup/rank_model.py:1 imports requests')
self_check('R3 catches a relative import',
           {'src/catchup/rank_model.py': 'from . import i18n\n'},
           'R3: src/catchup/rank_model.py:1 has a relative import')
self_check('R4 catches the harness opening its own HTTP path',
           {'bench/rank_eval.py': 'import http.client\n'},
           'R4: bench/rank_eval.py:1 imports http.client')
self_check('R4 catches the harness importing a request library',
           {'bench/rank_eval.py': 'import requests\n'},
           'R4: bench/rank_eval.py:1 imports requests')
self_check('R5 rejects affinity importing the ranker',
           {'src/affinity.py': 'import src.catchup.rank_model\n'},
           'R5: src/affinity.py:1 imports rank module rank_model')
self_check('R6 rejects tile constructors outside the shared adapter',
           {'src/catchup/tiles_render.py':
            'def build():\n    return statusactions.StatusActions()\n'},
           'R6: src/catchup/tiles_render.py:2 constructs StatusActions')
self_check('R7 rejects Catch Up importing window-owned navigation',
           {'src/catchup/pipeline.py': 'from .. import navigation\n'},
           'R7: src/catchup/pipeline.py:1 imports window-owned navigation')
self_check('R1 rejects affinity importing a Catch Up module',
           {'src/affinity.py': 'from .catchup import digest\n'},
           'R1: src/affinity.py:1 imports digest')
self_check('R1 rejects navigation importing a Catch Up submodule',
           {'src/navigation.py': 'from .catchup.store import X\n'},
           'R1: src/navigation.py:1 imports store')
self_check('R1 rejects feedchef package importing Catch Up fetch',
           {'src/feedchef/engine.py': 'from src.catchup import fetch\n'},
           'R1: src/feedchef/engine.py:1 imports fetch')
self_check('R2 rejects Catch Up importing feedchef',
           {'src/catchup/page.py': 'from src.feedchef import storage\n'},
           'R2: src/catchup/page.py:1 imports feedchef')
self_check('R8 rejects src/api importing GObject',
           {'src/api/statuses.py': 'import gi\n'},
           'R8: src/api/statuses.py:1 imports gi')
self_check('R8 rejects src/api importing the translation helper',
           {'src/api/statuses.py': 'from ..i18n import gettext\n'},
           'R8: src/api/statuses.py:1 imports ..i18n')
self_check('R8 rejects src/api importing the client',
           {'src/api/accounts.py': 'from .mastodon import MastodonClient\n'},
           'R8: src/api/accounts.py:1 imports .mastodon')
self_check('R8 rejects one resource module importing another',
           {'src/api/accounts.py': 'from . import statuses\n'},
           'R8: src/api/accounts.py:1 imports .')
self_check('R9 catches two modules importing each other at load time',
           {'src/alpha.py': 'from . import beta\n',
            'src/beta.py': 'import os\nfrom .alpha import thing\n'},
           'R9: load-time import cycle: alpha (src/alpha.py:1) -> '
           'beta (src/beta.py:2) -> alpha')
self_check('R9 follows a cycle through a subpackage and a class body',
           {'src/catchup/__init__.py': '',
            'src/catchup/tiles.py': 'from .. import gamma\n',
            'src/gamma.py': 'class C:\n    from .catchup import tiles\n'},
           'R9: load-time import cycle: catchup.tiles (src/catchup/'
           'tiles.py:1) -> gamma (src/gamma.py:2) -> catchup.tiles')
self_check('R9 catches a cycle through a package __init__',
           {'src/catchup/__init__.py': 'from .. import gamma\n',
            'src/catchup/tiles.py': '',
            'src/gamma.py': 'from .catchup.tiles import X\n'},
           'R9: load-time import cycle: catchup (src/catchup/__init__.py:1)'
           ' -> gamma (src/gamma.py:1) -> catchup')
check('R9 accepts a cycle closed by an import inside a function',
      not violations({'src/alpha.py': 'from . import beta\n',
                      'src/beta.py': 'def f():\n    from . import alpha\n'}))
check('R9 accepts a cycle closed under TYPE_CHECKING',
      not violations({'src/alpha.py': 'from . import beta\n',
                      'src/beta.py': 'from typing import TYPE_CHECKING\n'
                                     'if TYPE_CHECKING:\n'
                                     '    from . import alpha\n'}))
check('R9 accepts a submodule importing its own package',
      not violations({'src/catchup/__init__.py': 'from . import tiles\n',
                      'src/catchup/tiles.py': 'from . import rank\n',
                      'src/catchup/rank.py': ''}))
self_check('R10 catches the page toolkit importing a plugin module inside '
           'a function',
           {'src/linkrouting.py':
            'def open_web():\n    from . import pluginlinks\n'},
           'R10: src/linkrouting.py:2 imports pluginlinks')
self_check('R10 catches pages importing the web column',
           {'src/pages.py': 'from .webcolumn import present_web\n'},
           'R10: src/pages.py:1 imports webcolumn')
self_check('R10 catches linkhandlers importing a Babble module',
           {'src/linkhandlers.py': 'from . import appsettings\n'},
           'R10: src/linkhandlers.py:1 imports')
self_check('R10 catches linkhandlers importing outside the standard library',
           {'src/linkhandlers.py': 'import gi\n'},
           'R10: src/linkhandlers.py:1 imports gi')
check('R10 accepts the plugin modules importing the toolkit',
      not [line for line in violations({
          'src/pluginlinks.py': 'from . import linkhandlers\n',
          'src/linkhandlers.py': 'import types\n'})
          if line.startswith('R10')])
PAGES_SRC = {'src/pages.py': 'def reveal(): pass\nclass PagedList: pass\n'}
self_check('R11 catches an assignment to a moved name',
           {**PAGES_SRC, 'src/test-x.py':
            'from src import pages\npages.show_toast = lambda *a: None\n'},
           'R11: src/test-x.py:2 pages.show_toast')
self_check('R11 catches a read of a moved name',
           {**PAGES_SRC, 'src/x.py':
            'from . import pages\npages.open_link(1, 2)\n'},
           'R11: src/x.py:2 pages.open_link')
self_check('R11 catches a moved name imported from pages',
           {**PAGES_SRC, 'src/x.py': 'from .pages import connect_weak\n'},
           'R11: src/x.py:1 imports connect_weak from pages')
self_check('R11 catches the module imported under another name',
           {**PAGES_SRC, 'src/x.py':
            'from src import pages as P\nP.settings()\n'},
           'R11: src/x.py:2 pages.settings')
self_check('R11 catches src.pages spelled out',
           {**PAGES_SRC, 'src/x.py':
            'import src.pages\nsrc.pages.show_toast = None\n'},
           'R11: src/x.py:2 pages.show_toast')
self_check('R11 catches a getattr of a moved name',
           {**PAGES_SRC, 'bench/x.py':
            'from src import pages\ngetattr(pages, "settings")\n'},
           'R11: bench/x.py:2 pages.settings')
self_check('R10 catches importlib.import_module in the toolkit',
           {'src/feedback.py':
            'import importlib\n'
            'def f():\n    importlib.import_module("src.webcolumn")\n'},
           'R10: src/feedback.py:3 imports a module by name')
self_check('R10 catches __import__ in the toolkit',
           {'src/uihelpers.py': 'def f():\n    __import__("x")\n'},
           'R10: src/uihelpers.py:2 imports a module by name')
self_check('R10 covers the new toolkit modules',
           {'src/accountrows.py': 'from . import webcolumn\n'},
           'R10: src/accountrows.py:1 imports webcolumn')
check('R10 accepts importlib outside the toolkit',
      not [line for line in violations({
          'src/pluginhost.py': 'import importlib\nimportlib.import_module(1)\n'
          })
          if line.startswith('R10')])
self_check('R11 catches import src followed by src.pages.X',
           {**PAGES_SRC, 'src/x.py':
            'import src\nsrc.pages.show_toast = None\n'},
           'R11: src/x.py:2 pages.show_toast')
check('R11 accepts src.pages.<defined name>',
      not [line for line in violations({
          **PAGES_SRC, 'src/x.py': 'import src\nsrc.pages.reveal()\n'})
          if line.startswith('R11')])
check('R11 allows dunder attributes of pages',
      not [line for line in violations({
          **PAGES_SRC,
          'src/x.py': 'from . import pages\nprint(pages.__name__, '
                      'pages.__file__, getattr(pages, "__doc__"))\n'})
          if line.startswith('R11')])
self_check('R11 still catches a plain name next to a dunder',
           {**PAGES_SRC, 'src/x.py':
            'from . import pages\nprint(pages.__name__)\npages.nope\n'},
           'R11: src/x.py:3 pages.nope')
check('R11 sees a name pages defines under a top-level if or try',
      not [line for line in violations({
          'src/pages.py': 'if True:\n    def a(): pass\n'
                          'try:\n    b = 1\nexcept ImportError:\n    b = 2\n',
          'src/x.py': 'from . import pages\npages.a()\npages.b\n'})
          if line.startswith('R11')])
check('R11 sees names bound under a top-level for, with, while and match',
      not [line for line in violations({
          'src/pages.py':
              'for a in (1,):\n    b = a\nelse:\n    c = 1\n'
              'with open("x") as f:\n    d = 1\n'
              'while False:\n    e = 1\n'
              'match 1:\n    case 1:\n        g = 1\n'
              'async def runner():\n    pass\n',
          'src/x.py': 'from . import pages\n'
                      'pages.a, pages.b, pages.c, pages.d, pages.e, '
                      'pages.f, pages.g\n'})
          if line.startswith('R11')])
check('R11 sees match captures: a sequence, a star, a mapping rest, an as',
      not [line for line in violations({
          'src/pages.py':
              'match [1, 2]:\n    case [a, b]:\n        pass\n'
              '    case [first, *rest]:\n        pass\n'
              '    case {"k": v, **others}:\n        pass\n'
              '    case 1 as whole:\n        pass\n',
          'src/x.py': 'from . import pages\n'
                      'pages.a, pages.b, pages.first, pages.rest, pages.v, '
                      'pages.others, pages.whole\n'})
          if line.startswith('R11')])
check('R11 sees a walrus at the top level of pages.py',
      not [line for line in violations({
          'src/pages.py': 'if (n := 1):\n    pass\n'
                          'while (m := 0):\n    pass\n'
                          'total = [(k := i) for i in range(2)]\n',
          'src/x.py': 'from . import pages\npages.n, pages.m, pages.k\n'})
          if line.startswith('R11')])
self_check('R11 does not count a walrus inside a function or lambda',
           {'src/pages.py': 'def f():\n    if (hidden := 1):\n        pass\n'
                            'g = lambda: (also := 1)\n',
            'src/x.py': 'from . import pages\npages.hidden\n'},
           'R11: src/x.py:2 pages.hidden')
self_check('R11 still rejects an undefined name next to captures and walrus',
           {'src/pages.py': 'match 1:\n    case [a]:\n        pass\n'
                            'if (n := 1):\n    pass\n',
            'src/x.py': 'from . import pages\npages.nope\n'},
           'R11: src/x.py:2 pages.nope')
self_check('R11 still rejects a name bound nowhere next to those bodies',
           {'src/pages.py': 'with open("x") as f:\n    d = 1\n',
            'src/x.py': 'from . import pages\npages.nope\n'},
           'R11: src/x.py:2 pages.nope')
self_check('R11 does not count a def inside a function as defined',
           {'src/pages.py': 'def outer():\n    def inner(): pass\n',
            'src/x.py': 'from . import pages\npages.inner()\n'},
           'R11: src/x.py:2 pages.inner')
check('R11 accepts a name pages defines, and a local called pages',
      not [line for line in violations({
          **PAGES_SRC,
          'src/x.py': 'from . import pages\npages.reveal()\n'
                      'from .pages import PagedList\n',
          'src/y.py': 'pages = []\npages.append(1)\n'})
          if line.startswith('R11')])
self_check('R8 keeps the timelines exception to lists alone',
           {'src/api/timelines.py': 'from . import core, statuses\n'},
           'R8: src/api/timelines.py:1 imports .')
self_check('R8 keeps the lists exception to timelines alone',
           {'src/api/accounts.py': 'from . import lists\n'},
           'R8: src/api/accounts.py:1 imports .')
check('R8 accepts timelines importing lists',
      not [line for line in violations({
          'src/api/timelines.py': 'from . import core, lists\n'
                                  'from .lists import TIMELINE\n'})
          if line.startswith('R8')])
check('R8 accepts the standard library and core',
      not [line for line in violations({
          'src/api/accounts.py':
          'import dataclasses\nfrom typing import Optional\n'
          'from . import core\nfrom .core import send\n'})
          if line.startswith('R8')])
check('the checker accepts navigation importing the catchup package',
      not violations({'src/navigation.py':
                      'from .catchup import CatchUpPage\n'}))
check('the checker accepts the harness reaching the server through the'
      ' client',
      not violations({'bench/rank_eval.py': 'from src import mastodon\n'
                        'import urllib.parse\n'}))


def tree_sources():
    """The sources under the rules, keyed by the path shown in reports."""
    sources = {}
    for directory, _dirs, names in os.walk(srcdir):
        for name in sorted(names):
            if not name.endswith('.py'):
                continue
            absolute = os.path.join(directory, name)
            relative = os.path.relpath(absolute, root)
            with open(absolute, encoding='utf-8') as module:
                sources[relative] = module.read()
    for name in BENCH:
        path = os.path.join(root, 'bench', name)
        if os.path.exists(path):
            with open(path, encoding='utf-8') as module:
                sources['bench/' + name] = module.read()
    return sources


def extra_sources():
    """plugins/ and bench/ in full, for R11 only: R4 judges the harness
    files alone, and the fixture server legitimately speaks HTTP."""
    sources = {}
    for top in ('plugins', 'bench'):
        for directory, _dirs, names in os.walk(os.path.join(root, top)):
            for name in sorted(names):
                if name.endswith('.py'):
                    absolute = os.path.join(directory, name)
                    with open(absolute, encoding='utf-8') as module:
                        sources[os.path.relpath(absolute, root)] = \
                            module.read()
    return sources


SOURCES = tree_sources()
FOUND = violations(SOURCES)
FOUND.extend(pages_name_violations(
    {**extra_sources(), 'src/pages.py': SOURCES['src/pages.py']}))

unparsable = [line for line in FOUND if line.startswith('unparsable:')]
for line in unparsable:
    print('    %s' % line)
check('every source parses (%d unparsable)' % len(unparsable), not unparsable)

RULES = (
    ('R1', 'new ranked-place modules import only rank modules'),
    ('R2', 'Catch Up imports no ranked-place modules'),
    ('R3', 'rank modules import only standard library and siblings'),
    ('R4', 'benchmark reaches server through the shared client'),
    ('R5', 'affinity imports no ranker modules'),
    ('R6', 'Catch Up tiles construct controls through tileactions'),
    ('R7', 'Catch Up imports no window-owned module'),
    ('R8', 'src/api imports only the standard library and core'),
    ('R9', 'no import cycle among src modules at load time'),
    ('R10', 'the page toolkit imports no plugin or web-column module'),
    ('R11', 'nothing uses a name pages.py does not define'),
)
for rule, label in RULES:
    hits = [line for line in FOUND if line.startswith(rule + ':')]
    for line in hits:
        print('    %s' % line)
    check('%s %s (%d violations)' % (rule, label, len(hits)), not hits)

if kit.failures:
    print('%d checks failed' % kit.failures)
    sys.exit(1)
print('all module boundary checks passed')
