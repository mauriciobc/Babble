#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef is optional, and costs nothing while it is off.

Builds the real window with `feed-chef-enabled` off and asserts that no
Feed Chef module and no numpy was imported, no engine thread exists and the
sidebar has no Feed Chef entry; then turns it on live (the entry and a
pinned recipe page appear), and off again (both are gone, the engine of the
open recipe page is shut down). Prints, without asserting, what a recipe page
costs in resident memory. Exits 77 (skip) without a display."""

import importlib
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import tempfile
import threading

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, Gtk  # noqa: E402

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
Adw.init()
Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
Gio.Resource.load(bundle)._register()

from src import main as babble_main, mastodon  # noqa: E402
from src.appsettings import settings as app_settings  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def feedchef_modules():
    return sorted(name for name in sys.modules if 'feedchef' in name)


def feedchef_threads():
    return sorted(thread.name for thread in threading.enumerate()
                  if thread.name.lower().startswith('feedchef'))


def rss_kib():
    with open('/proc/self/statm') as handle:
        return int(handle.read().split()[1]) * os.sysconf('SC_PAGE_SIZE') \
            // 1024


settings = app_settings()

# The old hide-feed-chef preference carries over once.
settings.set_boolean('feed-chef-enabled', True)
settings.set_boolean('hide-feed-chef', False)
babble_main.migrate_hide_feed_chef(settings)
check('migration: a stored false changes nothing',
      settings.get_boolean('feed-chef-enabled')
      and settings.get_user_value('hide-feed-chef') is None)
settings.set_boolean('hide-feed-chef', True)
babble_main.migrate_hide_feed_chef(settings)
check('migration: a stored true turns Feed Chef off and resets the old key',
      not settings.get_boolean('feed-chef-enabled')
      and settings.get_user_value('hide-feed-chef') is None)
babble_main.migrate_hide_feed_chef(settings)
check('migration: with nothing stored it does nothing',
      not settings.get_boolean('feed-chef-enabled'))
settings.set_boolean('feed-chef-enabled', False)
settings.set_strv('favorite-feedchef-ids', ['1/some-recipe'])
threads_before = {thread.ident for thread in threading.enumerate()}

Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
window = BabbleWindow(application=Gio.Application.get_default())
window.main_stack.set_visible_child_name('shell')
window.client = mastodon.MastodonClient('https://example.org')
window.client.token = 'token'
window.account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
window.streaming.set_streams = lambda streams: None
window.streaming.start = lambda client: None
window.notifier.start_polling = lambda: None
window._refresh_sidebar()
items = window.sidebar_host._items

check('off: the key is off', not settings.get_boolean('feed-chef-enabled'))
check('off: no Feed Chef module was imported (%s)'
      % ', '.join(feedchef_modules()), not feedchef_modules())
check('off: numpy was not imported', 'numpy' not in sys.modules)
check('off: no Feed Chef thread',
      not feedchef_threads())
check('off: no thread name starts with feedchef',
      not [thread.name for thread in threading.enumerate()
           if thread.name.lower().startswith('feedchef')])
check('off: the sidebar has no Feed Chef entry', 'feed-chef' not in items)
check('off: and no pinned recipe entry, though one is stored',
      not [tag for tag in items if tag.startswith('recipe:')])
check('off: the place cannot be built',
      window.navigation.place_page('feed-chef') is None
      and window.navigation.place_page('recipe:some-recipe') is None)
app = Gio.Application.get_default()
toasts = []
window.add_toast = toasts.append
app._open_feedchef_uri(window, 'babble://feedchef?recipe=abc')
check('off: a babble://feedchef link offers to turn Feed Chef on',
      len(toasts) == 1 and toasts[0].get_button_label() == 'Turn On'
      and not settings.get_boolean('feed-chef-enabled'))
check('off: still no Feed Chef module after asking for them',
      not feedchef_modules())

# Storing a recipe imports Feed Chef, so it waits until the off checks are in.
from src.feedchef import storage as storagemod  # noqa: E402
from src.feedchef.recipe import default_recipe  # noqa: E402
storagemod.set_root(tempfile.mkdtemp(prefix='babble-feedchef-optional-'))
from src import accounts  # noqa: E402
from src import feedchefpages  # noqa: E402
from src.feedchef import deps  # noqa: E402
from src.feedchef.engine import FeedEngine  # noqa: E402

# No network: the page would preload the follow list and cook at once.
FeedEngine.init_preload = lambda self: None
feedchefpages.FeedChefTimelinePage.ensure_loaded = lambda self: None
from src import feedchefpreview  # noqa: E402
feedchefpreview.FeedChefPreviewPage.ensure_loaded = lambda self: None

recipe = default_recipe('Optional')
storagemod.FeedRecipeStorage(
    accounts.account_key(window.account)).save_recipe(recipe)
tag = 'recipe:%s' % recipe.id
settings.set_strv('favorite-feedchef-ids', ['1/%s' % recipe.id])
check('off: pinning a recipe adds nothing while off', tag not in items)

rss_off = rss_kib()
from src.feedchef.sharecodec import recipe_share_uri  # noqa: E402
presented = []
feedchefpages.present_import = lambda win, account, imported: \
    presented.append(imported.id)
toasts.clear()
app._open_feedchef_uri(window, recipe_share_uri(recipe))
check('off: the link toast was offered again', len(toasts) == 1)
toasts[0].emit('button-clicked')
check('Turn On sets the key and goes on to import the recipe',
      settings.get_boolean('feed-chef-enabled')
      and presented == [recipe.id])
items = window.sidebar_host._items
check('on: the sidebar shows Feed Chef', 'feed-chef' in items)
check('on: and the pinned recipe', tag in items)
page = window.navigation.place_page(tag)
child = window.navigation.children.get(tag)
check('on: the recipe page is built',
      page is not None
      and isinstance(child, feedchefpages.FeedChefTimelinePage))
engine = child.engine
engine._executor.submit(lambda: None).result()
check('on: the engine thread is named feedchef',
      any(name.startswith('feedchef-engine') for name in feedchef_threads()))
print('ok - rss delta: off %d KiB, on with a recipe page %d KiB (+%d KiB)'
      % (rss_off, rss_kib(), rss_kib() - rss_off))

# Dialogs with their own engines close when the key goes off.
from src.feedchefbuilder import FeedChefBuilderDialog  # noqa: E402
from src.feedchefpreview import FeedChefPreviewDialog  # noqa: E402
window.present()
preview = FeedChefPreviewDialog(window.client, window.account, recipe)
builder = FeedChefBuilderDialog(window.client, window.account, recipe)
closed = []
preview.connect('closed', lambda *_a: closed.append('preview'))
builder.connect('closed', lambda *_a: closed.append('builder'))
preview.present(window)
builder.present(window)
preview_engine = preview.timeline.engine

settings.set_boolean('feed-chef-enabled', False)
check('off again: open Feed Chef dialogs are closed',
      sorted(closed) == ['builder', 'preview'])
check('off again: the preview dialog\'s engine was shut down',
      preview_engine._executor._shutdown)
items = window.sidebar_host._items
check('off again: the entry is gone', 'feed-chef' not in items)
check('off again: the pinned recipe entry is gone', tag not in items)
check('off again: the recipe page is dropped',
      tag not in window.navigation.children
      and tag not in window.navigation.pages)
check('off again: its engine was shut down',
      engine._executor._shutdown and engine._cancel.is_set())

# The helper that loads the optional libraries.
real_import = importlib.import_module


def failing_import(name, *args):
    if name == 'numpy':
        raise ImportError('numpy is missing here')
    return real_import(name, *args)


importlib.import_module = failing_import
try:
    check('deps: a library that fails to import is None with a reason',
          deps.get('numpy', allow_main=True) is None
          and not deps.available('numpy', allow_main=True)
          and 'numpy is missing here' in deps.reason('numpy'))
finally:
    importlib.import_module = real_import
    deps._modules.pop('numpy', None)
    deps._reasons.pop('numpy', None)
check('deps: an unknown name is None and is not cached',
      deps.get('feedchef_no_such_library', allow_main=True) is None
      and 'feedchef_no_such_library' not in deps._modules)

if failures:
    raise SystemExit('%d feedchef optional checks failed' % len(failures))
print('all feedchef optional checks passed')
