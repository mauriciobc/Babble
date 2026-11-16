# SPDX-License-Identifier: GPL-3.0-or-later

"""The file size rule, as a test so it cannot drift silently.

No module the window was broken into shall grow past ``LIMIT`` lines.
They came out of window.py one seam each, and the size is what keeps a
candidate seam from quietly moving back in: growth in one of these
files is how a God class reappears without anybody calling it that.

window.py is a recorded exception rather than a silent pass, with the
reason we could not cut it further named right there rather than left
unrecorded for the next reader to re-litigate.

This is number of lines, which ruff covers through E501 (characters per
line), not module length -- ruff has no rule for the latter.

Every module over the limit is in one of the two lists below, with the
reason stated rather than left unrecorded. That closure is the newer half
of this test: before it, nine modules were over the limit and in neither
list, so the rule held for five of them and the rest could grow without
anyone being told. A module that is over the limit and undeclared now
fails the test, which is the point.
"""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import re
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))

LIMIT = 800

# The one directory of test-only support modules (never installed).
TESTKIT = 'testkit'

# One seam each, extracted from window.py. A growth in one of these is
# how a God class reappears without anybody calling it that.
SEAMS = ('portals.py', 'auth.py', 'drafts.py', 'clipboard.py',
         'navigation.py', 'sidebarhost.py', 'streamrouter.py')

# Recorded in CLAUDE.md, not silently tolerated. The reason is stated
# here, where a future reader will meet it, and mirrored in CLAUDE.md;
# the line count itself is what this test prints above, so it is not
# repeated here to go stale.
EXCEPTIONS = {}

failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL - %s' % label)
    else:
        print('ok - %s' % label)


for name in SEAMS:
    path = os.path.join(srcdir, name)
    lines = open(path, encoding='utf-8').read().count('\n')
    check('%s at %d lines stays under %d' % (name, lines, LIMIT),
          lines <= LIMIT)

for name, reason in EXCEPTIONS.items():
    path = os.path.join(srcdir, name)
    if not os.path.exists(path):
        # A module that has been split since this list was written: the
        # declaration was right to be removed, not left to rot.
        print('    %s: declared but gone -- drop this entry' % name)
        continue
    lines = open(path, encoding='utf-8').read().count('\n')
    # A declared exception that no longer exceeds the limit is stale: the
    # split happened, or the file shrank, and the entry should follow.
    check('%s is declared while over the limit' % name, lines > LIMIT)
    check('%s declares a reason' % name, bool(reason))
    if lines > LIMIT:
        print('    %s: %d lines. %s' % (name, lines, reason))

# The rule cannot drift back for a module that is in neither list, so
# every module has to be in one of them or under the limit. Before this,
# nine modules were over the limit and unlisted: the test passed, and a
# God class could reappear in any of them unnoticed.
root = os.path.dirname(os.path.abspath(srcdir))
every_python = sorted(
    os.path.relpath(os.path.join(directory, filename), srcdir)
    for directory, _dirs, files in os.walk(srcdir)
    for filename in files
    if filename.endswith('.py') and not filename.startswith('test-'))
# Test-only support lives in testkit/ next to the scripts that import it.
# It is size-checked like any module, but it is never installed and never
# reaches the catalogue, so install_data and POTFILES.in do not name it.
modules = [name for name in every_python if not name.startswith(TESTKIT + '/')]
testkit_files = [name for name in every_python
                 if name.startswith(TESTKIT + '/')]

# The limit itself, over everything the rule checks, is the reason the rule
# exists; the two lists above only narrate it. This is the line that fails
# when a split lands half-done. The scenario scripts and the bench harness
# are held to the same limit: a 3000-line test hides its own scenario
# boundaries as well as a 3000-line window did.
sized = [os.path.join(srcdir, name) for name in modules + testkit_files]
sized += [os.path.join(srcdir, name)
          for name in sorted(os.listdir(srcdir))
          if name.startswith('test-') and name.endswith('.py')]
sized.append(os.path.join(root, 'bench', 'rank_eval.py'))
for directory, _dirs, files in os.walk(os.path.join(root, 'bench',
                                                    'rankeval')):
    sized += [os.path.join(directory, name) for name in sorted(files)
              if name.endswith('.py')]
over = []
for path in sized:
    lines = open(path, encoding='utf-8').read().count('\n')
    if lines > LIMIT:
        over.append((path, lines))
for path, lines in over:
    print('    %s is %d lines' % (os.path.relpath(path, root), lines))
check('every file checked stays at or under %d (%d over)'
      % (LIMIT, len(over)), not over)
undeclared = []
for name in modules:
    lines = open(os.path.join(srcdir, name), encoding='utf-8').read().count('\n')
    if name.startswith('catchup/') and lines > LIMIT:
        undeclared.append((name, lines))
    elif lines > LIMIT and name not in EXCEPTIONS:
        undeclared.append((name, lines))

for name, lines in undeclared:
    print('    %s is %d lines and is in neither SEAMS nor EXCEPTIONS'
          % (name, lines))
check('every module over the limit declares why (%d undeclared)'
      % len(undeclared), not undeclared)



# install_data (src/meson.build) is maintained by hand and nothing checked
# it: a module left off it is missing from the installed app, and the
# failure appears only at runtime, on a user's machine, as an ImportError.
# The rule was in CLAUDE.md as prose while its siblings -- module length
# and gate spelling -- each had a test.

build_path = os.path.join(srcdir, 'meson.build')
with open(build_path, encoding='utf-8') as build_file:
    build_source = build_file.read()
installed = set(re.findall(r"'([a-z0-9_/]+\.py)'", build_source))
missing = sorted(name for name in modules
                 if name not in installed and name != '__init__.py')
for name in missing:
    print('    %s is not in src/meson.build install_data' % name)
check('every module is installed (%d missing)' % len(missing), not missing)
catchup_modules = sorted(
    os.path.relpath(os.path.join(directory, filename), srcdir)
    for directory, _dirs, files in os.walk(os.path.join(srcdir, 'catchup'))
    for filename in files if filename.endswith('.py'))
missing_catchup = sorted(name for name in catchup_modules
                         if name not in installed)
for name in missing_catchup:
    print('    %s is not in src/meson.build catchup install_data' % name)
check('every Catch Up submodule is installed (%d missing)'
      % len(missing_catchup), not missing_catchup)
stale = sorted(name for name in installed
               if name not in modules and name != 'meson.build')
for name in stale:
    print('    %s is in install_data but no longer exists' % name)
check('install_data names no module that is gone (%d stale)' % len(stale),
      not stale)

# po/POTFILES.in is maintained by hand too. A module that translates but is
# not listed loses its strings from the catalogue on the next regeneration,
# and a listed file that is gone stops xgettext altogether. Splitting
# timeline.py did both: the file was deleted while still listed, and nine
# new modules were never added.

potfiles_path = os.path.join(os.path.dirname(os.path.abspath(srcdir)),
                             'po', 'POTFILES.in')
with open(potfiles_path, encoding='utf-8') as potfiles:
    listed = {line.strip() for line in potfiles
              if line.strip() and not line.startswith('#')}
gone = sorted(path for path in listed
              if not os.path.exists(os.path.join(root, path)))
for path in gone:
    print('    %s is in POTFILES.in but does not exist' % path)
check('POTFILES.in names no file that is gone (%d missing)' % len(gone),
      not gone)
translates = re.compile(r'(?<![A-Za-z0-9_.])_\(|\bngettext\(')
# i18n.py defines the gettext helpers; it has no strings of its own.
unlisted = []
for name in modules:
    if os.path.basename(name) in ('__init__.py', 'i18n.py'):
        continue
    with open(os.path.join(srcdir, name), encoding='utf-8') as module:
        if translates.search(module.read()) \
                and 'src/' + name not in listed:
            unlisted.append(name)
for name in unlisted:
    print('    %s translates but is not in POTFILES.in' % name)
check('every module that translates is in POTFILES.in (%d unlisted)'
      % len(unlisted), not unlisted)

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('all file size checks passed')
