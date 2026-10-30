#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hashtag links in post text open Babble's tag timeline, not the
browser. Covers the parser's class signal, the body's href -> name map,
the URL-shape fallback, and the notification preview that used to reach
the default Gtk.Label handler. Exits 77 (skip) without a display."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

gresource = os.path.join(builddir, 'src', 'babble.gresource')
if os.path.exists(gresource):
    Gio.Resource.load(gresource)._register()

from src import hashtags, notifications, pages, statustext  # noqa: E402
from src import textemoji  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


routes = []
hashtags.push_hashtag = lambda _widget, _client, name: \
    routes.append(('tag', name))
pages.open_web = lambda _widget, uri: routes.append(('web', uri))
pages.launch_external = lambda _widget, uri: routes.append(('external', uri))


class TestWindow(Gtk.Window):
    """The smallest thing pages.window_of accepts: a root with a client
    and a navigation view."""

    def __init__(self):
        super().__init__()
        self.client = object()
        self.navigation_view = Adw.NavigationView()


win = TestWindow()

# The accented href whose status.tags entry is the folded "kunstliche..."
ACCENTED = ('https://m.remote/tags/K%C3%BCnstlicheIntelligenz',
            '#KünstlicheIntelligenz')
WAFRN = ('https://app.wafrn.net/dashboard/search/photo%20friday',
         '#photo-friday')

# Post body: the parser's class signal reaches open_link through the
# href -> name map, so the payload spelling never has to match.

body = textemoji.InlineEmojiText()
win.set_child(body)
body.set_segments(statustext.parse_content(
    '<p>look <a class="mention hashtag" href="%s">%s</a></p>'
    % ACCENTED))
check('the body maps the href to the displayed tag',
      body._link_hashtags.get(ACCENTED[0]) == 'KünstlicheIntelligenz')

routes.clear()
body._open(ACCENTED[0])
check('the body opens the tag page in-app',
      routes == [('tag', 'KünstlicheIntelligenz')])

# An anchor without the class still routes when the path is /tags/.

routes.clear()
pages.open_link(win, 'https://remote.test/tags/Ad%C3%A1s')
check('a /tags/ link without the class opens in-app',
      routes == [('tag', 'Adás')])

# A server that links tags through a search path only works through the
# class signal; without it, the link is an ordinary web link.

routes.clear()
pages.open_link(win, WAFRN[0], tag='photo-friday')
check('a tag link off the /tags/ path opens in-app when flagged',
      routes == [('tag', 'photo-friday')])

routes.clear()
pages.open_link(win, WAFRN[0])
check('the same link without the flag stays a web link',
      routes == [('web', WAFRN[0])])

# Ordinary links are unchanged.

routes.clear()
pages.open_link(win, 'https://example.org/article')
check('an ordinary link still goes to the web destination',
      routes == [('web', 'https://example.org/article')])

# The notification post preview used to hand links to Gtk.Label's
# default handler. It now routes them itself.

post = notifications.NotificationPost()
win.set_child(post)
post._show(statustext.parse_content(
    '<p>news <a class="hashtag" href="%s">%s</a></p>' % ACCENTED))
routes.clear()
handled = post.content.emit('activate-link', ACCENTED[0])
check('the notification preview handles the link itself', handled is True)
check('the notification preview opens the tag page in-app',
      routes == [('tag', 'KünstlicheIntelligenz')])

routes.clear()
handled = post.content.emit('activate-link', 'https://example.org/article')
check('the notification preview still handles ordinary links',
      handled is True and routes == [('web', 'https://example.org/article')])

win.destroy()

if failures:
    print('%d FAILED' % len(failures))
    sys.exit(1)
print('all hashtag link checks passed')
