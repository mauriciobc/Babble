#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Na Real hashtag banner, and what a closed panel leaves behind.

Exits 77 (skip) without a display, WebKitGTK 6.0 or libpeas 2."""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

from testkit import narealplugin as kit  # noqa: E402
from testkit.narealplugin import real_timers, timer_calls  # noqa: E402
from gi.repository import GLib, GObject, Gtk  # noqa: E402
from src import pluginmenu as pluginmenu_mod  # noqa: E402,F401
from testkit.narealplugin import (SERVER,
    Server,
    anchor,
    check,
    column,
    composed,
    context,
    detour,
    failures,
    feedback,
    freed,
    host,
    httpd,
    link_ext,
    linkrouting,
    main_ext,
    menu,
    module,
    open_questions,
    opened,
    panel_now,
    pluginmenu,
    real_compose,
    real_pages,
    resolved,
    settled,
    share_link,
    shut,
    spin,
    textemoji,
    toasts,
    wait_dom,
    window)  # noqa: E402
import gc

# --- The hashtag banner (its own commit) -----------------------------------

from src import hashtags  # noqa: E402


class _ApiClient:
    token = 'token'
    instance_info = None
    instance = 'https://example.org'

    def request_finish(self, result):
        return result

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def banner_of(page):
    """The page's Na Real extension (its widget is ``.list``)."""
    for slot in host.slots:
        if slot.box_ref() is page.plugin_banners:
            for extension, *_rest in slot.extensions.get(
                    'babble_nareal', []):
                return extension
    return None


client = _ApiClient()
Server.paths.clear()
page = hashtags.HashtagPage(client, ['nareal'])
banner = banner_of(page)
check('the configured hashtag gets the banner',
      page.plugin_banners.get_visible() and banner.list.get_visible()
      and banner.row.get_title() == '#nareal')
other_page = hashtags.HashtagPage(client, ['python'])
check('any other hashtag gets none',
      not other_page.plugin_banners.get_visible()
      and banner_of(other_page) is not None
      and not hasattr(banner_of(other_page), 'list'))
mixed = hashtags.HashtagPage(client, ['NaReal'])
check('the tag is matched whatever its case',
      mixed.plugin_banners.get_visible())
spin(0.7)
check('the banner asks the network nothing', Server.paths == [])
context.settings.set('hashtag', 'ask')
ask_page = hashtags.HashtagPage(client, ['ask'])
old_page = hashtags.HashtagPage(client, ['nareal'])
check('the hashtag setting moves it',
      ask_page.plugin_banners.get_visible()
      and not old_page.plugin_banners.get_visible())
context.settings.remove('hashtag')
del ask_page, old_page, other_page, mixed

recorded = []
real_open_panel = context.open_panel
context.open_panel = lambda widget, url, title=None: (
    recorded.append((url, title)),
    real_open_panel(anchor, url, title=title))[1]
banner.button.emit('clicked')
check('the banner button opens the dashboard as Na Real',
      recorded == [(SERVER + '/dashboard', 'Na Real')]
      and settled(SERVER + '/login'))
check('and that panel is scripted like any other',
      wait_dom("document.getElementById('instance').value",
               'mastodon.social'))
del context.open_panel
shut()
page_ref = page.weak_ref()
del page, banner
check('the page is freed', freed(page_ref))

# --- What is left behind ---------------------------------------------------

open_questions()
settled(SERVER + '/login')
check('an open panel is the current handle',
      context.data.get('panel') is panel_now())
column().close_column()  # the user hides the column
check('on_close clears the plugin’s state',
      spin(until=lambda: 'panel' not in context.data, seconds=5))

open_questions()
settled(SERVER + '/login')
first = panel_now()
linkrouting.open_link(anchor, SERVER + '/login?second', resolve=True)
settled(SERVER + '/login?second')
spin(0.5)
check('a replaced panel does not clear the newer one’s handle',
      not first.is_open and context.data.get('panel') is panel_now())
shut()

check('no stored widget: not in the shared data, nor on the extensions',
      not any(isinstance(v, Gtk.Widget) for v in context.data.values())
      and not any(isinstance(v, GObject.Object)
                  for ext in (main_ext, link_ext)
                  for v in vars(ext).values() if v is not ext.context))
frees = []
open_questions()
settled(SERVER + '/login')
column().weak_ref(lambda: frees.append(True))
linkrouting.open_link(anchor, SERVER + '/login?third', resolve=True)
settled(SERVER + '/login?third')
spin(0.5)
shut()
for _attempt in range(5):
    gc.collect()
    spin(0.1)
check('the web column is freed once the panel is closed', frees == [True])
check('and no script of the plugin starts a timer in the page',
      not any(word in value for name, value in vars(module).items()
              if name.endswith('_SCRIPT') and isinstance(value, str)
              for word in ('setTimeout', 'setInterval')))

# Disabling the plugin.
open_questions()
settled(SERVER + '/login')
dead_panel = panel_now()
count = (len(toasts), len(opened), len(composed))
Server.lag = 0.8
share_link()  # a probe in flight that would compose
Server.own_status = 404
share_link()  # and one that would open the dashboard and toast
host.set_enabled('babble_nareal', False)
check('disabling the plugin closes its panel',
      not dead_panel.is_open and column() is None)
spin(1.8)
Server.lag = 0.0
Server.own_status = 200
check('a probe in flight never opens a panel, a toast or the composer '
      'afterwards', column() is None
      and (len(toasts), len(opened), len(composed)) == count)
check('the main menu has no entries once it is disabled', menu() == [])
del resolved[:]
linkrouting.open_link(anchor, SERVER + '/@alice@mastodon.social', resolve=True)
check('and drops its claim on links',
      resolved == [SERVER + '/@alice@mastodon.social']
      and column() is None)
check('and nothing calls back',
      (len(toasts), len(opened), len(composed)) == count)
check('the plugin never used a timer', timer_calls == [])

GLib.timeout_add, GLib.timeout_add_seconds, GLib.idle_add = real_timers
pluginmenu.compose = real_compose
(feedback.show_toast, linkrouting.open_link, linkrouting.resolve_uri,
 textemoji.launch_uri) = real_pages
host.shutdown()
window.destroy()
httpd.shutdown()
detour.shutdown()
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all nareal banner checks passed')
