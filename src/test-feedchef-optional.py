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
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

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
real_present_import = feedchefpages.present_import
feedchefpages.present_import = lambda win, account, imported: \
    presented.append(imported.id)
toasts.clear()
app._open_feedchef_uri(window, recipe_share_uri(recipe))
check('off: the link toast was offered again', len(toasts) == 1)
toasts[0].emit('button-clicked')


def pump(done, seconds=5):
    # The link is read on a worker; the result comes back by idle.
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)
    return done()


pump(lambda: presented)
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

# Links: signed out, the worker, the order, a changed world.
from src.feedchef.sharecodec import MAX_LINK_CHARS  # noqa: E402
from src.feedchef import sharecodec  # noqa: E402
import gc  # noqa: E402

shown = []
real_show_toast = BabbleWindow.show_toast
real_add_toast = BabbleWindow.add_toast
real_try_parse = sharecodec.try_parse_feedchef_uri
thread_errors = []
real_excepthook = threading.excepthook
client, account = window.client, window.account


def sign_out():
    window.client = window.account = None
    window.main_stack.set_visible_child_name('login')


def sign_in():
    window.client, window.account = client, account
    window.main_stack.set_visible_child_name('shell')


def uri_of(name):
    return recipe_share_uri(default_recipe(name))


def fresh_state():
    shown.clear()
    presented.clear()
    toasts.clear()


def link_checks():
    settings.set_boolean('feed-chef-enabled', True)
    # On the class: the worker's idle gets the window back by weak
    # reference, which may be a new wrapper without instance attributes.
    BabbleWindow.show_toast = lambda _self, text: shown.append(text)
    BabbleWindow.add_toast = lambda _self, toast: toasts.append(toast)
    presented_names = []
    feedchefpages.present_import = lambda win, acct, imported: \
        presented_names.append(imported.meta.name) or presented.append(
            imported.id)

    # --- signed out: queued, then opened after sign-in ---
    sign_out()
    fresh_state()
    signed_out_uri = recipe_share_uri(recipe)
    app._open_feedchef_uri(window, signed_out_uri)
    pump(lambda: presented, 0.3)
    check('signed out: the link is queued, nothing opens yet',
          app._after_sign_in == [('feedchef', signed_out_uri)]
          and not presented and shown == ['Sign in to import this recipe'])
    app._open_feedchef_uri(window, signed_out_uri)
    check('signed out: the same link again is kept once and toasts once',
          len(app._after_sign_in) == 1 and len(shown) == 1)
    app.open_web_uri(window, 'web+mastodon://follow?uri=acct:a@example.org')
    check('signed out: web links share the one queue',
          [kind for kind, _x in app._after_sign_in] == ['feedchef', 'web'])
    app._after_sign_in = [entry for entry in app._after_sign_in
                          if entry[0] == 'feedchef']
    sign_in()
    check('signed in: the queue is emptied', app._after_sign_in == []
          and app._after_sign_in_handler is None)
    check('signed in: the queued link opens the import',
          pump(lambda: presented) and presented == [recipe.id])

    # --- Feed Chef off at sign-in, several links: one offer opens all ---
    sign_out()
    fresh_state()
    first, second = uri_of('One'), uri_of('Two')
    app._open_feedchef_uri(window, first)
    app._open_feedchef_uri(window, second)
    presented_names.clear()
    settings.set_boolean('feed-chef-enabled', False)
    toasts.clear()
    sign_in()
    pump(lambda: presented, 0.3)
    check('off at sign-in: nothing opens and there is one Turn On offer',
          not presented and len(toasts) == 1
          and toasts[0].get_button_label() == 'Turn On')
    toasts[0].emit('button-clicked')
    check('Turn On opens every waiting link, in order',
          pump(lambda: len(presented_names) == 2)
          and presented_names == ['One', 'Two'])
    fresh_state()

    # --- a link the parser raises on still gets an answer ---
    def exploding(_uri):
        raise RuntimeError('parser bug')

    sharecodec.try_parse_feedchef_uri = exploding
    threading.excepthook = lambda args: thread_errors.append(args)
    app._open_feedchef_uri(window, uri_of('Boom'))
    pump(lambda: shown, 3)
    check('a parser that raises: the reader gets the toast, not silence',
          shown == ['Could not read that Feed Chef link']
          and not thread_errors and not presented)
    sharecodec.try_parse_feedchef_uri = real_try_parse
    fresh_state()

    # --- the worker, the order ---
    threads = []
    gate = threading.Event()

    def slow_parse(link):
        threads.append(threading.current_thread())
        if 'SLOW' in link:
            gate.wait(3)
        else:
            time.sleep(0.1)
        return real_try_parse(link.replace('&SLOW=1', ''))

    sharecodec.try_parse_feedchef_uri = slow_parse
    presented_names.clear()
    app._open_feedchef_uri(window, uri_of('Slow') + '&SLOW=1')
    app._open_feedchef_uri(window, uri_of('Quick'))
    pump(lambda: False, 0.3)
    check('the second link does not overtake the first',
          presented_names == [])
    gate.set()
    pump(lambda: len(presented_names) == 2, 3)
    check('links open in the order received',
          presented_names == ['Slow', 'Quick'])
    check('both were read on one worker thread, never the main thread',
          len(set(threads)) == 1 and threads[0] is not
          threading.main_thread())

    # --- a 16 KiB link is read off the main thread, with no stall ---
    long_uri = 'babble://feedchef?recipe=' + 'A' * (MAX_LINK_CHARS - 40)
    gaps = []
    last = [time.perf_counter()]

    def tick():
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000)
        last[0] = now
        return True

    fresh_state()
    threads.clear()
    source = GLib.timeout_add(2, tick)
    app._open_feedchef_uri(window, long_uri)
    pump(lambda: shown, 5)
    pump(lambda: False, 0.1)
    GLib.source_remove(source)
    print('ok - worst main-loop gap while a link takes 100 ms to read: '
          '%.1f ms' % max(gaps))
    check('a 16 KiB link is read on a worker (slow parse stubbed)',
          threads and threads[0] is not threading.main_thread())
    check('a 16 KiB link is refused with the toast',
          shown == ['Could not read that Feed Chef link'] and not presented)
    check('no main-loop gap over 16 ms while it was read', max(gaps) <= 16)
    sharecodec.try_parse_feedchef_uri = real_try_parse
    fresh_state()
    app._open_feedchef_uri(window, 'babble://feedchef?recipe=' + 'A' * 20000)
    check('a link over the limit is refused on the main thread at once',
          shown == ['Could not read that Feed Chef link'])

    # --- the world changes while a link is being read ---
    def gated(link):
        gate.wait(3)
        return real_try_parse(link)

    sharecodec.try_parse_feedchef_uri = gated

    fresh_state()
    gate.clear()
    app._open_feedchef_uri(window, uri_of('Off'))
    pump(lambda: False, 0.1)
    settings.set_boolean('feed-chef-enabled', False)
    gate.set()
    pump(lambda: toasts, 2)
    check('turned off while reading: the Turn On offer, nothing opens',
          len(toasts) == 1 and not presented)
    settings.set_boolean('feed-chef-enabled', True)

    fresh_state()
    gate.clear()
    waiting_uri = uri_of('SignedOut')
    app._open_feedchef_uri(window, waiting_uri)
    pump(lambda: False, 0.1)
    sign_out()
    gate.set()
    pump(lambda: app._after_sign_in, 2)
    check('signed out while reading: the link is queued, not dropped',
          app._after_sign_in == [('feedchef', waiting_uri)]
          and not presented)
    sign_in()
    check('and it opens after the next sign-in',
          pump(lambda: presented))

    fresh_state()
    gate.clear()
    other = BabbleWindow(application=app)
    other.client, other.account = client, account
    app._open_feedchef_uri(other, uri_of('Closed'))
    pump(lambda: False, 0.1)
    other.destroy()
    del other
    gc.collect()
    gate.set()
    pump(lambda: presented or shown, 0.5)
    check('window closed while reading: nothing opens, nothing raises',
          not presented and not shown)
    sharecodec.try_parse_feedchef_uri = real_try_parse


try:
    link_checks()
finally:
    BabbleWindow.show_toast = real_show_toast
    BabbleWindow.add_toast = real_add_toast
    sharecodec.try_parse_feedchef_uri = real_try_parse
    threading.excepthook = real_excepthook
    feedchefpages.present_import = real_present_import
    settings.set_boolean('feed-chef-enabled', True)

# The import flow outlives its Python wrappers and is freed after use.
freed = []
fresh = default_recipe('Fresh')
dialog = real_present_import(window, window.account, fresh)
dialog.weak_ref(lambda: freed.append(True))
check('import dialog: the flow object is held by the dialog',
      isinstance(dialog._flow, feedchefpages._ImportFlow))
del dialog
for _round in range(5):
    gc.collect()
    pump(lambda: False, 0.05)
shown_dialog = window.get_visible_dialog()
check('import dialog survives the collector while it is shown',
      shown_dialog is not None and not freed)
storage = feedchefpages.storage_for(window.account)
shown_dialog.emit('response', 'import')
check('after the collector ran, Import still saves the recipe',
      storage.get_recipe_by_id(fresh.id) is not None)
shown_dialog.close()
del shown_dialog
for _round in range(5):
    gc.collect()
    pump(lambda: freed, 0.2)
check('import dialog is freed after an answer', bool(freed))

# The preview path: Preview, then the preview closes and the question
# comes back, all after the collector ran.
again = default_recipe('Again')
dialog = real_present_import(window, window.account, again)
dialog.emit('response', 'preview')
del dialog
for _round in range(5):
    gc.collect()
    pump(lambda: False, 0.05)
preview_dialog = window.get_visible_dialog()
check('preview: the preview dialog is shown',
      isinstance(preview_dialog, FeedChefPreviewDialog))
preview_dialog.force_close()
del preview_dialog
pump(lambda: False, 0.3)
back = window.get_visible_dialog()
check('preview closed: the import question comes back',
      isinstance(back, Adw.AlertDialog)
      and isinstance(back._flow, feedchefpages._ImportFlow))
back.force_close()
pump(lambda: False, 0.2)

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
