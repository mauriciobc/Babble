#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Hashpanel plugin against a fake Hashpanel on loopback: the
banner shows on the pages of the hashtags the schedule names and only
there, Hashpanel is never asked about any other tag, answers are reused
while fresh, a server that does not answer leaves no banner, and pages
are freed. Exits 77 (skip) without a display, or without libpeas 2."""

import gc
import http.server
import json
import os
import sys
import tempfile
import threading
import time

scratch = tempfile.mkdtemp(prefix='babble-hashpanel-')
os.environ['XDG_CONFIG_HOME'] = os.path.join(scratch, 'config')
os.environ['XDG_DATA_HOME'] = os.path.join(scratch, 'data')
os.environ.pop('BABBLE_PLUGIN_PATH', None)

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)
try:
    gi.require_version('Peas', '2')
    from gi.repository import Peas  # noqa: E402, F401
except (ImportError, ValueError):
    print('SKIP - libpeas 2 is not installed')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src import hashtags, pluginhost, pluginspage  # noqa: E402

Adw.init()
failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def spin(until, seconds=5.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if until():
            return True
        time.sleep(0.01)
    return until()


def freed(ref, attempts=5):
    for _attempt in range(attempts):
        gc.collect()
        gc.collect()
        spin(lambda: False, 0.05)
        if ref() is None:
            return True
    return ref() is None


# The fake Hashpanel: the live server's shapes, trimmed.

SCHEDULE = {'dailySchedule': [
    {'day': 1, 'name': 'Monday', 'hashtag': 'segundaficha',
     'hashtags': ['segundaficha'], 'isToday': True},
    {'day': 2, 'name': 'Tuesday', 'hashtag': 'tercinema',
     'hashtags': ['tercinema', 'tersoftware'], 'isToday': False},
]}
STATS = {
    'hashtag': 'segundaficha', 'timeframe': 'today',
    'summary': {'tootCount': 12, 'uniqueUsers': 8},
    'engagement': {'totalFavorites': 4, 'totalBoosts': 7,
                   'totalReplies': 1},
    'topToots': [{'id': '1', 'author': 'autobrain',
                  'content': '<p>Jogo todo dia o Termo</p><p><a href="x">'
                             '#<span>SegundaFicha</span></a></p>',
                  'link': 'https://example.org/@autobrain/1'}],
}
WEEKLY = {'weeklyData': [{'weekNumber': 42, 'totalUses': 1},
                         {'weekNumber': 43, 'totalUses': 14}]}


class Server:
    hits = []  # paths asked for
    delay = 0.0


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        Server.hits.append(self.path)
        time.sleep(Server.delay)
        if self.path == '/api/hashtag/daily':
            body = SCHEDULE
        elif self.path.endswith('/stats'):
            body = STATS
        elif self.path.endswith('/history/weekly'):
            body = WEEKLY
        else:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
SERVER = 'http://127.0.0.1:%d' % httpd.server_address[1]


def hits(fragment):
    return sum(1 for path in Server.hits if fragment in path)


class _ApiClient:
    token = 'token'
    instance_info = None
    instance = 'https://example.org'

    def request_async(self, *_args, **_kwargs):
        pass

    def request_finish(self, result):
        return result

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


host = pluginhost.host()
check('the host starts', host.start())
check('the Hashpanel plugin is shipped and found',
      host.engine.get_plugin_info('babble_hashpanel') is not None)
host.set_enabled('babble_hashpanel', True)
check('it loads', 'babble_hashpanel' in host.contexts)
module = sys.modules['babble_hashpanel']
context = host.contexts['babble_hashpanel']
context.settings.set('server', SERVER)
opened = []
context.open_link = lambda widget, url: opened.append(url)

check('the default server is the live Hashpanel',
      module.DEFAULT_SERVER == 'https://hashpanel.mauriciocastro.des.br')
check('weekdays are named in the interface language',
      module.weekday_name(0) == 'Sunday'
      and module.weekday_name(2) == 'Tuesday')
check('a post is shortened to plain text',
      module.plain_text(STATS['topToots'][0]['content'])
      == 'Jogo todo dia o Termo #SegundaFicha')
check('the schedule maps each tag to its days',
      module.parse_schedule(SCHEDULE)['tersoftware']
      == {'days': [2], 'today': False})
check('a schedule of another shape is no schedule',
      module.parse_schedule({'error': 'x'}) is None)

client = _ApiClient()


def banner_of(page):
    """The page's Hashpanel extension (its widget is ``.list``)."""
    for slot in host.slots:
        if slot.box_ref() is page.plugin_banners:
            for extension, *_rest in slot.extensions.get(
                    'babble_hashpanel', []):
                return extension
    return None


# A tag Hashpanel does not follow.
page = hashtags.HashtagPage(client, ['python'])
spin(lambda: hits('/daily') == 1)
spin(lambda: False, 0.2)
check('the schedule is asked for once', hits('/daily') == 1)
check('a tag it does not follow gets no banner',
      not page.plugin_banners.get_visible()
      and not banner_of(page).list.get_visible())
check('and Hashpanel is never asked about it', hits('python') == 0)
page_ref = page.weak_ref()
del page
check('that page is freed', freed(page_ref))

# Today's tag.
page = hashtags.HashtagPage(client, ['SegundaFicha'])
banner = banner_of(page)
spin(lambda: banner.week.get_visible() and banner.top.get_visible())
check('a followed tag gets the banner', page.plugin_banners.get_visible()
      and banner.list.get_visible())
check('the schedule is not asked for again', hits('/daily') == 1)
check("today's tag says so", banner.header.get_title() == 'Today’s Hashtag')
check("with today's favorites, boosts and replies",
      banner.header.get_subtitle()
      == 'Today: 4 favorites · 7 boosts · 1 reply')
check('the week against the one before',
      banner.week.get_subtitle() == '14 posts this week · 1 the week before')
check('and the top post, as text',
      banner.top.get_subtitle() == '@autobrain: Jogo todo dia o Termo '
      '#SegundaFicha')
banner.top.emit('activated')
check('the top post opens through Babble',
      opened == ['https://example.org/@autobrain/1'])
banner.open_button.emit('clicked')
check('the open button opens Hashpanel', opened[-1] == SERVER)
stats_asked = hits('/stats')
page_ref = page.weak_ref()
page = banner = None
check('the page is freed', freed(page_ref))

page = hashtags.HashtagPage(client, ['segundaficha'])
banner = banner_of(page)
spin(lambda: banner.top.get_visible())
check('reopening it within two minutes asks nothing again',
      hits('/stats') == stats_asked and banner.top.get_visible())
page = banner = None

# Another weekday's tag.
page = hashtags.HashtagPage(client, ['tersoftware'])
banner = banner_of(page)
spin(lambda: banner.week.get_visible())
check("another day's tag is named by its day",
      banner.header.get_title() == 'Tuesday’s Hashtag')
page = banner = None

# An answer in flight when its page closes.
Server.delay = 0.3
module.source_of(context).forget()
page = hashtags.HashtagPage(client, ['tercinema'])
page_ref = page.weak_ref()
del page
check('a page whose answers are in flight is freed', freed(page_ref))
spin(lambda: False, 0.8)
Server.delay = 0.0

# A server that does not answer.
module.source_of(context).forget()
context.settings.set('server', 'http://127.0.0.1:9')
page = hashtags.HashtagPage(client, ['segundaficha'])
spin(lambda: False, 1.0)
check('no Hashpanel, no banner', not page.plugin_banners.get_visible())
del page
context.settings.set('server', SERVER)

# Its settings.
window = Adw.Window(default_width=600, default_height=700)
window.set_content(Gtk.Label())
window.present()
plugins = pluginspage.present_plugins(window)
row = [r for r in plugins.rows if r.plugin_id == 'babble_hashpanel'][0]
check('the Plugins page offers its settings',
      row.settings_button.get_visible())
row.settings_button.emit('clicked')
preferences = [ext for slot in host.slots if slot.plugin_id
               for ext, *_rest in slot.extensions.get('babble_hashpanel', [])]
settings_page = preferences[0]
check('they show the server in use',
      settings_page.server.get_text() == SERVER)
settings_page.server.set_text('http://hashpanel.example')
settings_page.server.emit('apply')
check('plain http to another machine is refused',
      settings_page.error.get_visible()
      and context.settings.get('server') == SERVER)
module.source_of(context).cache['x'] = (time.monotonic() + 60, {})
settings_page.server.set_text('hashpanel.example/')
settings_page.server.emit('apply')
check('a new server is saved, as https',
      context.settings.get('server') == 'https://hashpanel.example'
      and not settings_page.error.get_visible())
check('and what the old one said is forgotten',
      not module.source_of(context).cache)
settings_page.server.set_text(module.DEFAULT_SERVER)
settings_page.server.emit('apply')
check('the default is not stored as a setting',
      context.settings.get('server') is None)
del preferences, settings_page, row
plugins.close()
spin(lambda: window.get_visible_dialog() is None, 3)
plugins_ref = plugins.weak_ref()
del plugins
check('the closed Plugins page is freed', freed(plugins_ref))

host.shutdown()
window.destroy()
httpd.shutdown()
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
