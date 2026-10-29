#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The window's side of place routing: what `PlaceHost` is wired to.

`test-navigation` drives `Navigation` against a stand-in host, which cannot
notice a callback wired to the wrong window method. This builds the real
window and follows the local and federated places through it: building
them, the stream and auto-refresh the window starts for them, closing one
the way the sidebar does when the server will not serve it, and the
teardown on an account switch. Exits 77 (skip) without a display."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

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
Adw.init()
Gio.Resource.load(bundle)._register()

from src import main as babble_main, mastodon  # noqa: E402
from src.streaming import FEDERATED, LOCAL  # noqa: E402
from src.timelinepage import TimelinePage  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
window = BabbleWindow(application=Gio.Application.get_default())
window.main_stack.set_visible_child_name('shell')
window.client = mastodon.MastodonClient('https://example.org')
window.client.token = 'token'
window.account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}
window.settings.set_boolean('live-updates', True)
window.settings.set_boolean('public-live-updates', True)
window.settings.set_string('auto-refresh', '60')

wanted = []
window.streaming.set_streams = lambda streams: wanted.append(set(streams))
window.streaming.start = lambda client: None
window.notifier.start_polling = lambda: None

routing = window.navigation
host = routing.host
check('the host is wired to the window, method for method',
      host.make_sidebar_control == window.make_sidebar_control
      and host.timeline_place == window._timeline_place
      and host.restricted_public_places == window._restricted_public_places
      and host.notify_place_built == window._public_place_built
      and host.sync_streaming == window._sync_streaming)
check('and hands out the window\'s client and account as they are now',
      host.client() is window.client and host.account() is window.account)
switched = {'id': '2', 'acct': 'you', 'instance': 'https://example.org'}
window.account = switched
check('including after an account switch', host.account() is switched)
window.account = {'id': '1', 'acct': 'me', 'instance': 'https://example.org'}

local = routing.place_page('local')
federated = routing.place_page('federated')
check('the window builds both public places', local and federated)
check('they are timeline pages, found by tag',
      set(routing.public_pages) == {'local', 'federated'}
      and all(isinstance(page, TimelinePage)
              for page in routing.public_pages.values()))
check('the window counts them among its timeline pages',
      all(page in window._timeline_pages()
          for page in routing.public_pages.values()))
check('it started a stream for each',
      bool(wanted) and {LOCAL, FEDERATED} <= wanted[-1])
check('and switched on the auto-refresh of each',
      all(page._auto_refresh_id for page in routing.public_pages.values()))

# A feed the server will not serve: the sidebar refresh drops its page,
# its child and its stream, and leaves the other alone.
window._restricted_public_places = lambda: {'local'}
window._refresh_sidebar()
window._sync_streaming()
check('a closed feed keeps no page',
      'local' not in routing.pages and 'local' not in routing.children
      and 'local' not in routing.public_pages
      and 'federated' in routing.public_pages)
check('and no stream', LOCAL not in wanted[-1] and FEDERATED in wanted[-1])

window._teardown_session()
check('an account switch empties every cache',
      not routing.pages and not routing.children
      and not routing.public_pages)
window._restricted_public_places = lambda: set()
check('and a place can be built again afterwards',
      routing.place_page('local') is not None
      and 'local' in routing.public_pages)

if failures:
    raise SystemExit('%d navigation window checks failed' % len(failures))
print('all navigation window checks passed')
