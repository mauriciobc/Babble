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
import re
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))

LIMIT = 800

# One seam each, extracted from window.py. A growth in one of these is
# how a God class reappears without anybody calling it that.
SEAMS = ('portals.py', 'auth.py', 'drafts.py', 'clipboard.py')

# Recorded in CLAUDE.md, not silently tolerated. The reason is stated
# here, where a future reader will meet it, and mirrored in CLAUDE.md;
# the line count itself is what this test prints above, so it is not
# repeated here to go stale.
EXCEPTIONS = {
    'pages.py': 'the shared page toolkit: navigation, feedback, the toast '
                'and error surfaces, the paged list and the paging state '
                'machine every status list shares (CLEAN-08). It grew by '
                'carrying the paging base. ARCH-06 split the timeline '
                'around it; the state machine stays here because every '
                'status list uses it.',
    'window.py': 'four of five seams extracted (portals, auth, drafts, '
                 'clipboard); the remaining lines are view routing over '
                 'page containers the window owns, and extracting them '
                 'would invert streaming.py\'s dependency on that state '
                 'rather than cut a seam.',
    'compose.py': 'the composer widget. ARCH-01 moved the upload round '
                  'to composeupload.py; what remains is the draft, the '
                  'options and the post itself, which is one dialog.',
    'profile.py': 'the profile header and tabs. ARCH-02 moved the '
                  'relationship commands to profileactions.py; the '
                  'header, fields and tabs are the page that is left.',
    'notifications.py': 'the notification rows and the policy dialog. '
                        'ARCH-03 moved paging, filter, unread and clear '
                        'to notificationctrl.py.',
    'admin.py': 'the moderation dashboard: seven tools, each a page of '
                'its own, behind one authorization. Splitting it buys '
                'readability, not a seam, so it is declared.',
    'mediaviewer.py': 'the picture viewer: zoom and pan geometry, '
                      'video and audio playback, drag sources and temp '
                      'file pruning. One widget, one concern.',
    'fedicollections.py': 'Collections (Mastodon 4.6+): page, CRUD and '
                          'membership.',
    'mastodon.py': 'the API client: OAuth, the keyring, the transport, '
                   'and one method per endpoint the app calls.',
    'hashtags.py': 'the followed-tag list plus a hashtag timeline. '
                   'The timeline is TimelinePage (ARCH-06); the list '
                   'and the saved-definition editor are this module.',
}

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
modules = sorted(
    os.path.relpath(os.path.join(root, filename), srcdir)
    for root, _dirs, files in os.walk(srcdir)
    for filename in files
    if filename.endswith('.py') and not filename.startswith('test-'))
undeclared = []
for name in modules:
    lines = open(os.path.join(srcdir, name), encoding='utf-8').read().count('\n')
    if lines > LIMIT and name not in EXCEPTIONS:
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
root = os.path.dirname(os.path.abspath(srcdir))
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
