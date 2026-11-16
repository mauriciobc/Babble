#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden request log: the exact wire requests each UI action sends.

The API-layer refactor (Docs/api-layer-plan.md, "Behaviour-preserving
proof") moves request construction out of the widgets and out of
``mastodon.py``. This test records, for each named scenario, every request
the real UI code (StatusActions, StatusMenuButton, ProfileActions, the
`src.api` functions and the MastodonClient transport) hands to the
transport and compares it with the committed
``golden/api-requests.json``. After a refactor the log must be identical;
a difference is a behaviour change.

Nothing touches the network. The recorder replaces the single transport
chokepoint, ``MastodonClient._spawn(task, message)``, which every request
passes through once libsoup's message is built. It records what is on that
message -- method, path, sorted query, headers and body (form pairs, JSON
or the multipart field names) -- then completes the Gio.Task with a canned
answer, so the UI's own callbacks (merge, rollback, toast, Undo, the 404
fallback) run for real. Dialogs and toasts are the only stubs: a
confirmation runs its action at once, and a toast hands the test its
Undo and timeout.

What is pinned, and what is not. Every header except Content-Length is
recorded, so a legitimate refactor that adds or changes a header (a new
User-Agent, an Accept) changes the golden deliberately and the diff is the
review. The order of form pairs is not pinned: they are compared sorted, so
a refactor may build a body in another order. Out of scope, because they
do not pass through ``MastodonClient._spawn`` as one UI action: the 429
retry respawn, the streaming socket, the ``imagecache`` and ``mediaviewer``
sessions, and the plugin host.

Every scenario must produce at least one request, unless it is named in
``ALLOW_NO_REQUESTS`` with its reason.

    BABBLE_GOLDEN_UPDATE=1   rewrite golden/api-requests.json
    BABBLE_GOLDEN_MUTATE=1   corrupt one recorded path, to show the test
                             fails with a readable diff (used by hand, to
                             prove the test can fail)

Exits 77 (skip) without a display or a compiled bundle."""

import difflib
import json
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, Gio  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

Adw.init()

from testkit import golden_scenarios1  # noqa: E402,F401
from testkit import golden_scenarios2  # noqa: E402,F401
from testkit import golden_scenarios3  # noqa: E402,F401
from testkit import goldenkit as kit  # noqa: E402
from testkit.goldenkit import Recorder  # noqa: E402


# Run

recorded = {}
for title, function in kit.SCENARIOS:
    kit.CURRENT = rec = Recorder()
    function(rec)
    rec.settle()
    recorded[title] = list(rec.log)
    if title not in kit.ALLOW_NO_REQUESTS:
        kit.check('%s sends at least one request' % title,
                  any('method' in entry for entry in rec.log))

if kit.UPDATE:
    os.makedirs(os.path.dirname(kit.GOLDEN), exist_ok=True)
    with open(kit.GOLDEN, 'w', encoding='utf-8') as handle:
        json.dump({'scenarios': recorded}, handle, indent=1,
                  sort_keys=True)
        handle.write('\n')
    for title, log in recorded.items():
        print('note - %s: %d request(s) written' % (title, len(log)))
    print('note - wrote %s' % kit.GOLDEN)
else:
    if not os.path.exists(kit.GOLDEN):
        print('FAIL - %s is missing; run with BABBLE_GOLDEN_UPDATE=1'
              % kit.GOLDEN)
        sys.exit(1)
    with open(kit.GOLDEN, encoding='utf-8') as handle:
        expected = json.load(handle)['scenarios']

    def dump(log):
        return json.dumps(log, indent=1, sort_keys=True).splitlines()

    for title in recorded:
        if title not in expected:
            kit.check('%s (%d requests)' % (title, len(recorded[title])),
                      False, '    not in the golden file')
            continue
        diff = list(difflib.unified_diff(
            dump(expected[title]), dump(recorded[title]),
            'golden', 'recorded', lineterm='', n=2))
        kit.check('%s (%d requests)' % (title, len(recorded[title])),
                  not diff, '\n'.join('    ' + line for line in diff))
    for title in expected:
        if title not in recorded:
            kit.check('%s is still recorded' % title, False,
                      '    in the golden file but no longer driven')

if kit.failures:
    print('%d failed' % len(kit.failures))
    sys.exit(1)
