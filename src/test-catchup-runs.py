#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Catch Up page against a fake client: the first-run page, a full run
that writes a snapshot, the All posts list and its filters and sorts, the
Highlights slot staying put while they change, cancel and 429 keeping what
was gathered, snapshot retention and reopening, and the cleanup when an
account is removed. Also the pieces under the page that need no widget
(ordering, the snapshot files, the run's stop logic).

Exits 77 (skip) without a display."""

import gc
import json
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import shutil
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import accounts  # noqa: E402
from src.catchup import store as catchupstore  # noqa: E402

from testkit import catchuppage as kit  # noqa: E402
from testkit.catchuppage import (ACCOUNT,
    CACHE,
    FakeClient,
    MemorySettings,
    _catchup_tiles_saved,
    check,
    make_page,
    settle)  # noqa: E402

# A catch-up that cannot be written still shows, and says so.
write_failed = catchupstore.write_snapshot


def refuse(_snapshot):
    raise OSError(28, 'No space left on device')


page4, host4 = make_page(FakeClient(), dict(ACCOUNT, id='77'))
settle(lambda: page4.state == 'first-run')
catchupstore.write_snapshot = refuse
try:
    page4.catch_up_button.emit('clicked')
    settle(lambda: page4.state == 'results')
finally:
    catchupstore.write_snapshot = write_failed
check('a catch-up that could not be saved says so, and still shows',
      page4._save_failed and page4.save_banner.get_revealed()
      and page4.store.get_n_items() > 1)

# Cancel in the middle of a run
client = FakeClient(page_size=10)
page3, host3 = make_page(client, dict(ACCOUNT, id='55'))
settle(lambda: page3.state == 'first-run')
client.on_page = lambda number: (
    page3._on_cancel_clicked(None) if number == 2 else None)
page3.catch_up_button.emit('clicked')
settle(lambda: page3.state == 'results')
snap = catchupstore.list_snapshots('https://test.example#55')
check('Cancel shows and saves what was gathered',
      page3.state == 'results' and snap
      and snap[0][1]['stopped'] == 'cancelled' and snap[0][1]['count'] == 20)
check('and says how far back it got',
      page3.note_label.get_visible()
      and page3.note_label.get_label().startswith('Covers the last')
      and '(20 posts)' in page3.note_label.get_label())
page3.cancel()

# 429 in the middle
client = FakeClient(page_size=10)
client.fail_page = (3, 429)
page4, host4 = make_page(client, dict(ACCOUNT, id='56'))
settle(lambda: page4.state == 'first-run')
page4.catch_up_button.emit('clicked')
settle(lambda: page4.state == 'results')
snap = catchupstore.list_snapshots('https://test.example#56')
check('a 429 keeps the posts and saves them as rate-limited',
      snap and snap[0][1]['stopped'] == 'rate-limited'
      and snap[0][1]['count'] == 20)
check('and shows a banner with the wait',
      page4.limit_banner.get_revealed()
      and '2 minutes' in page4.limit_banner.get_title())
page4.cancel()

# Nothing gathered: an error page, nothing saved
client = FakeClient()
client.fail_page = (1, 500)
page5, host5 = make_page(client, dict(ACCOUNT, id='57'))
settle(lambda: page5.state == 'first-run')
page5.catch_up_button.emit('clicked')
settle(lambda: page5.state == 'error')
check('a failure before any post shows the error page and saves nothing',
      page5.state == 'error'
      and not catchupstore.list_snapshots('https://test.example#57'))
page5.cancel()

# A page closed mid-run stops asking and saves nothing
client = FakeClient(page_size=10)
page6, host6 = make_page(client, dict(ACCOUNT, id='58'))
settle(lambda: page6.state == 'first-run')
client.on_page = lambda number: page6.cancel() if number == 2 else None
page6.catch_up_button.emit('clicked')
settle(lambda: False, timeout=0.5)
check('a cancelled page asks for nothing more and saves nothing',
      client.paths().count('/timelines/home') == 2
      and '/followed_tags' not in client.paths()
      and not catchupstore.list_snapshots('https://test.example#58'))

# --------------------------------------------------------------------------
# Account removal


gone = {'id': '56', 'instance': 'https://test.example'}
kept_account = {'id': '55', 'instance': 'https://test.example'}
settings = MemorySettings([json.dumps(gone), json.dumps(kept_account)])
check('before removal the account has snapshots',
      catchupstore.list_snapshots('https://test.example#56'))
accounts.forget_account(settings, '56', 'https://test.example')
check('removing an account deletes its snapshots',
      not os.path.exists(catchupstore.account_dir('https://test.example#56')))
check('and only its own',
      catchupstore.list_snapshots('https://test.example#55'))

# --------------------------------------------------------------------------
# The real Highlights grid under the page

sys.modules.pop('src.catchup.tiles', None)
from src.catchup import tiles as catchup_tiles  # noqa: E402
from src.catchup import tiles_render as catchup_tiles_render  # noqa: E402

page8, host8 = make_page(FakeClient(), dict(ACCOUNT, id='60'))
window8 = Gtk.Window(default_width=700, default_height=900, child=host8)
window8.present()
settle(lambda: page8.state == 'first-run')
page8.catch_up_button.emit('clicked')
settle(lambda: page8.state == 'results')
view8 = page8._highlights
check('the Highlights slot holds a real HighlightsView',
      isinstance(view8, catchup_tiles.HighlightsView)
      and hasattr(view8, 'grid'))
grid8 = view8.grid
settle(lambda: any(p.kind == 'post' for p in grid8.placed)
       and len(grid8.tiles) > 0)
tiles = list(grid8.tiles.values())
children = []
child = grid8.get_first_child()
while child is not None:
    children.append(child)
    child = child.get_next_sibling()
check('the grid has a placed post tile',
      any(p.kind == 'post' for p in grid8.placed) and any(
          isinstance(t, catchup_tiles_render.PostTile) for t in tiles))
check('the tiles are children of the grid',
      tiles and all(t.get_parent() is grid8 for t in tiles))
check('no tile is the same widget twice',
      len({id(w) for w in children}) == len(children) == len(tiles))

page8.cancel()
refs = [page8.weak_ref(), host8.weak_ref(), view8.weak_ref(),
        grid8.weak_ref()] + [t.weak_ref() for t in tiles]
window8.set_child(None)
window8.destroy()
settle(lambda: False, timeout=0.5)
tiles = children = child = view8 = grid8 = page8 = host8 = None
window8 = None
gc.collect()
check('a dropped page frees its Highlights view, grid and tiles',
      all(ref() is None for ref in refs))

shutil.rmtree(CACHE, ignore_errors=True)

if _catchup_tiles_saved is not None:
    sys.modules['src.catchup.tiles'] = _catchup_tiles_saved
else:
    sys.modules.pop('src.catchup.tiles', None)


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all catchup runs checks passed')
