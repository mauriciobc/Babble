#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The web column: navigation policy, the widget, and that it is freed.

Exits 77 when no display is available or WebKitGTK cannot be loaded."""

import gc
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gio, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

resource = Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))
resource._register()

from src import pages, webcolumn  # noqa: E402

if not webcolumn.available():
    print('SKIP - WebKitGTK 6.0 is not available')
    sys.exit(77)

from src.webcolumn import (  # noqa: E402
    EXTERNAL, IGNORE, LOAD, ROUTE, classify_navigation)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


# Navigation policy

CTRL = int(Gdk.ModifierType.CONTROL_MASK)
WEB = 'https://example.org/page'

# The real fediverse predicate may live in another change; the table's
# ROUTE rows need one either way.
real_predicate = getattr(pages, 'is_fediverse_link', None)
pages.is_fediverse_link = lambda uri: '/@' in uri
FEDI = 'https://example.social/@user/123'

table = [
    ('empty', dict(uri=''), IGNORE, ''),
    ('about top-level', dict(uri='about:blank'), LOAD, 'about:blank'),
    ('about popup', dict(uri='about:blank', new_window=True), IGNORE,
     'about:blank'),
    ('data frame', dict(uri='data:text/html,x'), LOAD, 'data:text/html,x'),
    ('blob popup', dict(uri='blob:https://a/1', new_window=True), IGNORE,
     'blob:https://a/1'),
    ('mailto gesture', dict(uri='mailto:a@b.c', user_gesture=True),
     EXTERNAL, 'mailto:a@b.c'),
    ('mailto script', dict(uri='mailto:a@b.c'), IGNORE, 'mailto:a@b.c'),
    ('file gesture', dict(uri='file:///etc/passwd', user_gesture=True),
     EXTERNAL, 'file:///etc/passwd'),
    ('middle click', dict(uri=WEB, link_clicked=True, button=2),
     EXTERNAL, WEB),
    ('ctrl click', dict(uri=WEB, link_clicked=True, modifiers=CTRL),
     EXTERNAL, WEB),
    ('ctrl without click', dict(uri=WEB, modifiers=CTRL), LOAD, WEB),
    ('fediverse resolve', dict(uri=FEDI, link_clicked=True, resolve=True),
     ROUTE, FEDI),
    ('fediverse no resolve', dict(uri=FEDI, link_clicked=True), LOAD, FEDI),
    ('fediverse redirect', dict(uri=FEDI, resolve=True), LOAD, FEDI),
    ('popup script', dict(uri=WEB, new_window=True), IGNORE, WEB),
    ('popup gesture', dict(uri=WEB, new_window=True, user_gesture=True),
     LOAD, WEB),
    ('strip clicked', dict(uri=WEB + '?utm_source=x&a=1',
                           link_clicked=True, strip=True),
     LOAD, WEB + '?a=1'),
    ('strip redirect', dict(uri=WEB + '?utm_source=x', strip=True), LOAD,
     WEB + '?utm_source=x'),
    ('plain', dict(uri=WEB), LOAD, WEB),
]
for name, kwargs, action, uri in table:
    got = classify_navigation(kwargs.pop('uri'), **kwargs)
    check('classify %s' % name, got == (action, uri))
    if got != (action, uri):
        print('  got %r, wanted %r' % (got, (action, uri)))

if real_predicate is not None:
    pages.is_fediverse_link = real_predicate
    check('real predicate: post routes',
          classify_navigation('https://mastodon.social/@Gargron/1',
                              link_clicked=True, resolve=True)[0] == ROUTE)
    check('real predicate: article loads',
          classify_navigation('https://example.org/blog/post',
                              link_clicked=True, resolve=True)[0] == LOAD)


# Widget

Adw.init()
Gtk.init()


def pump(done, timeout=10):
    context = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + timeout * 1000000
    while not done() and GLib.get_monotonic_time() < end:
        context.iteration(False)
        GLib.usleep(2000)
    return done()


class Host(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.navigation_view = object()  # what pages.window_of looks for
        self.web_slot = Adw.Bin()
        self.web_split = Adw.OverlaySplitView(
            sidebar=self.web_slot, content=Gtk.Button(label='timeline'),
            show_sidebar=False, sidebar_position=Gtk.PackType.END,
            collapsed=False, min_sidebar_width=360, max_sidebar_width=720,
            sidebar_width_fraction=0.45, enable_show_gesture=False)
        self.set_default_size(1000, 600)
        self.set_child(self.web_split)
        webcolumn.watch_split(self.web_split)


host = Host()
host.present()
# The split view mis-sizes a sidebar shown before the window has been
# allocated once; the app only opens the column on a mapped window.
pump(lambda: host.get_mapped() and host.get_width() > 0, 5)

webcolumn.present_web(host, 'about:blank')
column = host.web_slot.get_child()
check('present_web builds the column', column is not None)
check('sidebar shown', host.web_split.get_show_sidebar())

view = column.view
loaded = []


def on_changed(_view, event):
    if event == column._webkit.LoadEvent.FINISHED:
        loaded.append(True)


view.connect('load-changed', on_changed)
view.load_html('<title>Hello</title><p>x', 'about:blank')
check('page finishes loading', pump(lambda: bool(loaded)))
check('title shown',
      pump(lambda: column.title.get_title() == 'Hello', 10))

webcolumn.present_web(host, 'about:blank')
check('column reused', host.web_slot.get_child() is column)

view_settings = view.get_settings()
check('media stream off', not view_settings.get_enable_media_stream())
check('playback needs a gesture',
      view_settings.get_media_playback_requires_user_gesture())
check('developer extras off', not view_settings.get_enable_developer_extras())
pages.settings().set_boolean('web-javascript', False)
check('javascript follows the setting',
      not view_settings.get_enable_javascript())
pages.settings().set_boolean('web-javascript', True)
check('javascript follows the setting back',
      view_settings.get_enable_javascript())
check('back disabled on first page', not column.back_button.get_sensitive())

# Error page
column._show_error('Title', 'Body', 'https://example.org', reload=True)
check('error page shown', column.stack.get_visible_child_name() == 'error')

# Closing frees the column
ref = column.weak_ref()
column.dispose_column()
check('close empties the slot', host.web_slot.get_child() is None)
view = column = loaded = None
for _ in range(20):
    GLib.MainContext.default().iteration(False)
    gc.collect()
check('closed column is freed', ref() is None)

# The split view hiding its own sidebar disposes it too
webcolumn.present_web(host, 'about:blank')
column = host.web_slot.get_child()
ref = column.weak_ref()
host.web_split.set_show_sidebar(False)
check('hiding the sidebar empties the slot', host.web_slot.get_child() is None)
column = None
for _ in range(20):
    GLib.MainContext.default().iteration(False)
    gc.collect()
check('hidden column is freed', ref() is None)



# Policy handler glue, with fake decisions

from gi.repository import WebKit  # noqa: E402

launched = []
routed = []
pages.launch_external = lambda _widget, uri: launched.append(uri)
pages.open_link = lambda _widget, uri, **_kw: routed.append(uri)


class FakeRequest:
    def __init__(self, uri):
        self.uri = uri

    def get_uri(self):
        return self.uri


class FakeAction:
    def __init__(self, uri, clicked=False, gesture=False, frame='',
                 redirect=False):
        self.uri, self.clicked, self.gesture = uri, clicked, gesture
        self.frame, self.redirect = frame, redirect

    def get_request(self):
        return FakeRequest(self.uri)

    def get_navigation_type(self):
        return (WebKit.NavigationType.LINK_CLICKED if self.clicked
                else WebKit.NavigationType.OTHER)

    def is_user_gesture(self):
        return self.gesture

    def get_mouse_button(self):
        return 0

    def get_modifiers(self):
        return 0

    def get_frame_name(self):
        return self.frame

    def is_redirect(self):
        return self.redirect


class FakeDecision:
    def __init__(self, action=None):
        self.action, self.verdict = action, None

    def get_navigation_action(self):
        return self.action

    def use(self):
        self.verdict = 'use'

    def ignore(self):
        self.verdict = 'ignore'


class FakeHeaders:
    def __init__(self, disposition):
        self.disposition = disposition

    def get_one(self, name):
        return self.disposition if name == 'Content-Disposition' else None


class FakeResponse:
    def __init__(self, uri, disposition=None):
        self.uri, self.disposition = uri, disposition

    def get_uri(self):
        return self.uri

    def get_http_headers(self):
        return FakeHeaders(self.disposition) if self.disposition else None


class FakeResponseDecision(FakeDecision):
    def __init__(self, uri, supported=True, main=True, disposition=None):
        super().__init__()
        self.supported, self.main = supported, main
        self.response = FakeResponse(uri, disposition)

    def is_main_frame_main_resource(self):
        return self.main

    def is_mime_type_supported(self):
        return self.supported

    def get_response(self):
        return self.response


NAV = WebKit.PolicyDecisionType.NAVIGATION_ACTION
POPUP = WebKit.PolicyDecisionType.NEW_WINDOW_ACTION
RESPONSE = WebKit.PolicyDecisionType.RESPONSE

webcolumn.present_web(host, 'about:blank')
column = host.web_slot.get_child()
loads = []
column.view.load_uri = loads.append  # the view is a Python attribute
config = pages.settings()
config.set_boolean('strip-tracking', True)
config.set_boolean('aggressive-resolving', True)
pages.is_fediverse_link = lambda uri: '/@' in uri


def decide(action, kind=NAV):
    decision = FakeDecision(action)
    handled = column._on_decide_policy(column.view, decision, kind)
    return handled, decision.verdict


del loads[:]
check('policy: plain navigation is used',
      decide(FakeAction(WEB)) == (True, 'use') and not loads)
tracked = WEB + '?utm_source=x&a=1'
check('policy: user click is stripped, ignored and reloaded',
      decide(FakeAction(tracked, clicked=True, gesture=True))
      == (True, 'ignore') and loads == [WEB + '?a=1'])
del loads[:]
check('policy: a script navigation is not stripped',
      decide(FakeAction(tracked)) == (True, 'use') and not loads)
check('policy: a click in a named frame is not stripped',
      decide(FakeAction(tracked, clicked=True, gesture=True, frame='f'))
      == (True, 'use') and not loads)
check('policy: external scheme with a gesture',
      decide(FakeAction('mailto:a@b.c', gesture=True)) == (True, 'ignore')
      and launched == ['mailto:a@b.c'])
del launched[:]
check('policy: external scheme without a gesture is dropped',
      decide(FakeAction('mailto:a@b.c')) == (True, 'ignore')
      and not launched)
check('policy: fediverse link is routed',
      decide(FakeAction(FEDI, clicked=True, gesture=True))
      == (True, 'ignore') and routed == [FEDI])
check('policy: popup with a gesture loads in the view',
      decide(FakeAction(WEB, gesture=True), POPUP) == (True, 'ignore')
      and loads == [WEB])
del loads[:]
check('policy: popup without a gesture is blocked',
      decide(FakeAction(WEB), POPUP) == (True, 'ignore') and not loads)


# Unsupported types and attachments

def respond(uri, **kwargs):
    decision = FakeResponseDecision(uri, **kwargs)
    return column._on_response(decision), decision.verdict


column._committed = True  # not a first load: nothing closes the column
column._user_nav = False
check('response: supported type is left to WebKit',
      respond('https://example.org/a', supported=True) == (False, None))
check('response: subframe resource is left alone',
      respond('https://example.org/a', supported=False, main=False)
      == (False, None))
check('response: unsupported type without a gesture is ignored, not launched',
      respond('https://example.org/a.bin', supported=False)
      == (True, 'ignore') and not launched)
column._user_nav = True
check('response: attachment with a gesture is launched',
      respond('https://example.org/b', disposition='attachment; filename=x')
      == (True, 'ignore') and launched == ['https://example.org/b'])
respond('https://example.org/b2', supported=False)
check('response: only once per navigation',
      launched == ['https://example.org/b'])
column._start_navigation(FakeAction(WEB, gesture=True))
respond('https://example.org/c', supported=False)
check('response: a new navigation may hand off again',
      launched == ['https://example.org/b', 'https://example.org/c'])
check('response: a redirect is not a new navigation',
      (column._start_navigation(FakeAction(WEB, redirect=True)),
       respond('https://example.org/d', supported=False))[1]
      == (True, 'ignore') and len(launched) == 2)
column._start_navigation(FakeAction(WEB))
check('response: a scripted navigation is not user-initiated',
      not column._user_nav)

# A first load that turns out to be a download closes the column
del launched[:]
column._committed = False
column._start_navigation(FakeAction(WEB, gesture=True))
respond('https://example.org/file.zip', supported=False)
check('response: first-load download hands off',
      launched == ['https://example.org/file.zip'])
check('response: first-load download closes the column',
      pump(lambda: host.web_slot.get_child() is None, 5)
      and not host.web_split.get_show_sidebar())
column = None


# Context menu

webcolumn.present_web(host, 'about:blank')
column = host.web_slot.get_child()
Action = WebKit.ContextMenuAction
check('every dropped context menu action exists',
      all(hasattr(Action, n) for n in (
          'OPEN_LINK_IN_NEW_WINDOW', 'DOWNLOAD_LINK_TO_DISK',
          'DOWNLOAD_IMAGE_TO_DISK', 'DOWNLOAD_AUDIO_TO_DISK',
          'DOWNLOAD_VIDEO_TO_DISK')))


class FakeHit:
    def __init__(self, link=None):
        self.link = link

    def context_is_link(self):
        return self.link is not None

    def get_link_uri(self):
        return self.link


def stock_menu():
    return WebKit.ContextMenu.new_with_items([
        WebKit.ContextMenuItem.new_from_stock_action(a) for a in (
            Action.OPEN_LINK, Action.OPEN_LINK_IN_NEW_WINDOW,
            Action.DOWNLOAD_LINK_TO_DISK, Action.COPY_LINK_TO_CLIPBOARD,
            Action.DOWNLOAD_IMAGE_TO_DISK)])


menu = stock_menu()
check('context menu: handler lets WebKit show it',
      column._on_context_menu(column.view, menu, FakeHit()) is False)
kept = [item.get_stock_action() for item in menu.get_items()]
check('context menu: new-window and download items are dropped',
      kept == [Action.OPEN_LINK, Action.COPY_LINK_TO_CLIPBOARD])
menu = stock_menu()
column._on_context_menu(column.view, menu, FakeHit(WEB))
items = menu.get_items()
check('context menu: a link gets Open Link in Browser',
      len(items) == 3 and items[-1].get_gaction() is not None
      and items[-1].get_gaction().get_name() == 'open-link')
del launched[:]
column._on_open_link(None, GLib.Variant('s', WEB))
check('context menu: Open Link in Browser launches the link',
      launched == [WEB])


# Forward window actions to the column

calls = []
column._on_back = lambda *_a: calls.append('back')
column._on_reload = lambda *_a: calls.append('reload')
column.view.grab_focus()
pump(lambda: host.get_focus() is not None, 3)
check('focus is in the column', host.get_focus() is not None
      and host.get_focus().is_ancestor(column))
check('forward_action: back goes to the column',
      webcolumn.forward_action(host, 'back') and calls == ['back'])
check('forward_action: reload goes to the column',
      webcolumn.forward_action(host, 'reload') and calls[-1] == 'reload')
host.web_split.get_content().grab_focus()
check('forward_action: not with the focus elsewhere',
      not webcolumn.forward_action(host, 'back') and len(calls) == 2)
column.view.grab_focus()
host.web_split.set_show_sidebar(False)
check('forward_action: not when the column is hidden',
      host.web_slot.get_child() is None
      and not webcolumn.forward_action(host, 'back'))
webcolumn.present_web(host, 'about:blank')
column = host.web_slot.get_child()
column.view.grab_focus()
pump(lambda: host.get_focus() is not None, 3)
check('forward_action: close hides and frees the column',
      webcolumn.forward_action(host, 'close')
      and host.web_slot.get_child() is None
      and not host.web_split.get_show_sidebar())


# Collapsing keeps the page; closing is only ever explicit

split = host.web_split
webcolumn.present_web(host, 'about:blank')
column = host.web_slot.get_child()
split.set_collapsed(True)
pump(lambda: False, 0.2)
check('collapse: the column survives', host.web_slot.get_child() is column)
check('collapse: and is hidden over the content',
      not split.get_show_sidebar())
split.set_collapsed(False)
pump(lambda: False, 0.2)
check('expand: the same column is pinned again',
      host.web_slot.get_child() is column and split.get_show_sidebar())
split.set_collapsed(True)
pump(lambda: False, 0.2)
webcolumn.present_web(host, 'about:blank')
check('collapsed: present_web shows it as an overlay',
      split.get_show_sidebar() and host.web_slot.get_child() is column)
split.set_show_sidebar(False)
check('collapsed: dismissing the overlay closes the column',
      host.web_slot.get_child() is None)
split.set_collapsed(False)
pump(lambda: False, 0.2)
check('expand after a close leaves no empty column',
      not split.get_show_sidebar())
split.set_collapsed(True)
split.set_collapsed(False)
pump(lambda: False, 0.2)
check('collapse and expand with nothing open stays hidden',
      not split.get_show_sidebar())
column = None

host.destroy()

if failures:
    print('%d failed' % len(failures))
    sys.exit(1)
