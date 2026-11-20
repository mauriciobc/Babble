#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The web column's PiP card: handover, restore, close, routing, and
that the column is freed.

Exits 77 when no display is available or WebKitGTK cannot be loaded.
The lifecycle cases run without media; the playback case serves its own
loopback page with a small clip and needs no codec beyond what the base
plugins decode."""

import gc
import http.server
import json
import time
import importlib.util
import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys
import threading

srcdir = os.environ.get('BABBLE_SRCDIR') or os.path.dirname(
    os.path.abspath(__file__))
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gio, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

resource = Gio.Resource.load(os.path.join(builddir, 'src', 'babble.gresource'))
resource._register()

from src import mediaviewer, webcolumn, webpip  # noqa: E402

if not webcolumn.available():
    print('SKIP - WebKitGTK 6.0 is not available')
    sys.exit(77)

from src.webcolumn import WebColumn  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''))


def pump(done, timeout=10):
    context = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + timeout * 1000000
    while not done() and GLib.get_monotonic_time() < end:
        context.iteration(False)
        GLib.usleep(2000)
    return done()


def settle(seconds):
    pump(lambda: False, seconds)


# A loopback page with the test's video in it. The clip is the media
# fixture's: Theora in Ogg, base plugins only. /slow.html trickles to
# keep a load running for the deferred-teardown case. /late.html adds
# its video after the load, so only the watcher's listeners can fill it.
VIDEO = ('<video id="v" muted autoplay loop playsinline src="clip.ogg" '
         'style="width:480px;height:270px"></video>')
PAGE = '<title>Pip page</title><p style="margin:0">scroll</p>' + VIDEO
LATE = ('<title>Late page</title><script>setTimeout(() => document.body'
        '.insertAdjacentHTML("beforeend", %s), 500)</script>'
        % json.dumps(VIDEO))

MEDIA = {}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/slow.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', '2048')
            self.end_headers()
            try:
                for _ in range(4):
                    self.wfile.write(b'x' * 512)
                    self.wfile.flush()
                    threading.Event().wait(0.4)
            except OSError:
                pass
            return
        if path == '/plain.html':
            body = b'<title>Plain page</title><p style="margin:0">x</p>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ('/page.html', '/late.html'):
            body = (PAGE if path == '/page.html' else LATE).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == '/clip.ogg' and MEDIA:
            data = MEDIA['data']
            start = 0
            end = len(data) - 1
            range_header = self.headers.get('Range')
            if range_header and range_header.startswith('bytes='):
                first = range_header.split('=', 1)[1].split('-', 1)
                try:
                    start = int(first[0])
                except ValueError:
                    start = 0
                if len(first) > 1 and first[1]:
                    end = min(int(first[1]), len(data) - 1)
                self.send_response(206)
                self.send_header(
                    'Content-Range', 'bytes %d-%d/%d'
                    % (start, end, len(data)))
            else:
                self.send_response(200)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Type', 'video/ogg')
            self.send_header('Content-Length', str(end - start + 1))
            self.end_headers()
            self.wfile.write(data[start:end + 1])
            return
        self.send_response(404)
        self.send_header('Content-Length', '0')
        self.end_headers()


spec = importlib.util.spec_from_file_location(
    'media_fixture', os.path.join(srcdir, 'test-media-fixture.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
try:
    MEDIA['data'] = open(fixture.write_clip(), 'rb').read()
except RuntimeError as error:
    print('FAIL - the test clip could be read [%s]' % error)
    sys.exit(1)

server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
BASE = 'http://127.0.0.1:%d' % server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()

# The window: an Adw window whose content is the overlay the card needs,
# hosting the same web_split the real shell builds.
Adw.init()
Gtk.init()


class Host(Adw.ApplicationWindow):
    def __init__(self):
        super().__init__()
        self.navigation_view = object()  # what pagenav.window_of looks for
        self.web_slot = Adw.Bin()
        self.web_split = Adw.OverlaySplitView(
            sidebar=self.web_slot, content=Gtk.Button(label='timeline'),
            show_sidebar=False, sidebar_position=Gtk.PackType.END,
            collapsed=False, min_sidebar_width=360, max_sidebar_width=720,
            sidebar_width_fraction=0.45, enable_show_gesture=False)
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.web_split)
        self.set_content(self.overlay)
        self.set_default_size(1000, 600)
        webcolumn.watch_split(self.web_split)


host = Host()
host.present()
pump(lambda: host.get_mapped() and host.get_width() > 0, 5)


def open_column(uri, wait=True):
    """A column showing *uri*; settled when *wait* (the deferred-close
    case opens a page still trickling in, so it must not)."""
    webcolumn.present_web(host, uri)
    column = host.web_slot.get_child()
    loaded = []

    def on_changed(_v, event):
        if event == column._webkit.LoadEvent.FINISHED:
            loaded.append(True)
            # Disconnected once the load settled: a plain connect that
            # holds *column* would keep the view <-> column cycle alive
            # past the dispose, which the app itself never does (all of
            # its view connects are weak).
            column.view.disconnect(on_changed_id)

    on_changed_id = column.view.connect('load-changed', on_changed)
    if wait:
        pump(lambda: bool(loaded), 15)
    return column


def card_buttons(card):
    """The OSD bar's two buttons, expand then close."""
    model = card.observe_children()
    for i in range(model.get_n_items()):
        child = model.get_item(i)
        if isinstance(child, Gtk.Widget) \
                and child.has_css_class('pip-controls'):
            first = child.get_first_child()
            return first, first.get_next_sibling()
    return None, None


def ask_true(column, script, timeout=10):
    """Run *script* in the card's isolated world, True when it answers
    truthy. A page that never answers is cancelled, so the pending
    evaluate cannot keep the column alive through its callback."""
    done = []
    cancellable = Gio.Cancellable()

    def answered(value, error):
        done.append(bool(value and value.to_boolean() and not error))
    column.evaluate(webpip.WORLD, script, answered, cancellable)
    if not pump(lambda: bool(done), timeout):
        cancellable.cancel()
        settle(0.3)
    return bool(done and done[0])


LOADS = {'n': 0}


def ask_true_until(column, script, seconds=10):
    """Ask until *script* answers truthy or *seconds* pass: the
    card's poll fills at its own cadence (3s), so a single ask races
    it."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ask_true(column, script, 2):
            return True
        settle(0.4)
    return ask_true(column, script, 2)


def counting_load(column):
    """Count the column's loads, so expand is proven not to reload."""
    original = WebColumn.load_uri

    def wrapper(*args, **kwargs):
        LOADS['n'] += 1
        return original(column, *args, **kwargs)
    column.load_uri = wrapper


# ---------------------------------------------------------------- 0
# A video added after the load is filled by the watcher's listeners
# alone (the poll is stopped). First, and handed on by expanding: under
# the test compositor a view made after another closed lays out at 0x0.
column = open_column(BASE + '/plain.html')
card = webpip.open_card(host)
card._stop_polling()
column.evaluate(None, 'location.href = "' + BASE + '/late.html"', None)
check('a video added after the load self-filled via the listeners',
      ask_true_until(column,
                     '!!document.querySelector("video[data-babble-pip-style]")',
                     30))
webpip.restore(card)
card = None

# ---------------------------------------------------------------- 1
# Minimize: the column moves into the card, the slot empties, the
# sidebar hides, nothing is disposed.
column = open_column(BASE + '/page.html')
check('the column built', isinstance(column, WebColumn))
ref = column.weak_ref()
counting_load(column)

card = webpip.open_card(host)
check('open_card returns the card', card is not None)
if card is not None:
    check('the column is inside the fixed-size viewport',
          card.get_child() is card._viewport
          and card._viewport.get_child().get_child() is column
          and column.get_parent() is card._viewport.get_child())
    check('focus polling remains active inside viewport',
          card._on_poll() == GLib.SOURCE_CONTINUE)
    check('the slot is empty', host.web_slot.get_child() is None)
    check('the sidebar hid', not host.web_split.get_show_sidebar())
    check('the column is not disposed', not column._disposed)
    check('no teardown timer runs', not column._teardown_timer)
    check('card mode is on', column.card_mode)
    check('the top bars hid', not column.header.get_visible())
    check('the card is registered', webpip.card_for(host) is card)
    check('the card is in the window overlay',
          card.get_parent() is host.overlay)
    check('the card asks 320x180',
          card.get_size_request()
          == (webpip.CARD_WIDTH, webpip.CARD_HEIGHT))

    pump(lambda: card.get_width() > 0 and card.get_height() > 0, 3)
    check('card allocation is 320x180',
          (card.get_width(), card.get_height())
          == (webpip.CARD_WIDTH, webpip.CARD_HEIGHT),
          '%dx%d' % (card.get_width(), card.get_height()))
    # Less free space than the corner margin: the near inset is what is
    # left, never a forced 24px that pushes the card past the edge.
    card.set_size_request(host.get_width() - 10, webpip.CARD_HEIGHT)
    card._corner_key(Gdk.KEY_Right)
    check('the near-corner inset clamps to the free space',
          card.get_margin_end() == 10, repr(card.get_margin_end()))
    card.set_size_request(webpip.CARD_WIDTH, webpip.CARD_HEIGHT)
    card._corner_key(Gdk.KEY_Right)
    expand, close = card_buttons(card)
    check('the OSD has expand and close',
          isinstance(expand, Gtk.Button)
          and expand.get_icon_name() == 'babble-pip-out-symbolic'
          and isinstance(close, Gtk.Button)
          and close.get_icon_name() == 'window-close-symbolic')
    check('every button has a tooltip',
          all(b.get_tooltip_text() for b in (expand, close)))
    check('focus moved to the card\'s expand button',
          host.get_focus() is expand or expand.has_focus(),
          repr(host.get_focus()) if expand is not None else 'no expand')
    del expand, close

# ---------------------------------------------------------------- 2
# The video focus: the page's muted autoplay is waited for (its start
# time varies with the codec probe), then the card's pick runs and the
# fill appears; the video keeps playing in the card.
check('the page video autoplays',
      ask_true(column, '!document.querySelector("video").paused', 20))
card._focus_video()
settle(1.5)
check('the video focus fill is applied',
      ask_true_until(column,
                     '!!document.querySelector("video[data-babble-pip-style]")'))
check('the fill leaves only the video on screen',
      ask_true(column, '(() => {'
                       'const v = document.querySelector('
                       '"video[data-babble-pip-style]");'
                       'const html = getComputedStyle(document.documentElement);'
                       'const body = getComputedStyle(document.body);'
                       'const p = document.querySelector("p");'
                       'return html.overflow === "hidden"'
                       ' && body.overflow === "hidden"'
                       ' && body.visibility === "hidden"'
                       ' && getComputedStyle(p).visibility === "hidden"'
                       ' && getComputedStyle(v).visibility === "visible";'
                       '})()'))
check('the video focus resists player inline styles',
      ask_true(column, '(() => {'
                       'const v = document.querySelector('
                       '"video[data-babble-pip-style]");'
                       'v.style.setProperty("position", "absolute", "important");'
                       'const focused = getComputedStyle(v).position === "fixed";'
                       'v.style.removeProperty("position");'
                       'return focused;'
                       '})()'))
check('the video keeps playing in the card',
      ask_true(column, '!document.querySelector("video").paused'))

# A load that happens while the card is out gets the watcher from the
# injected user script, not from us: navigate the page to itself (a
# fresh load), wait for its autoplay, and the fill must appear on its
# own.
column.evaluate(None, 'location.href = '
                + '"' + BASE + '/page.html?" + Date.now()', None)
check('the in-card load self-filled via the user script',
      ask_true_until(column,
                     '!!document.querySelector("video[data-babble-pip-style]")',
                     30))
column.evaluate(None, webpip.UNDO_SCRIPT, None)

# ---------------------------------------------------------------- 3
# A plugin panel page refuses the card.


class FakePanel:
    def __init__(self):
        self.ended = []

    def _end(self, why):
        self.ended.append(why)


panel = FakePanel()
webcolumn.present_panel(host, BASE + '/plain.html', panel)
check('a panel page hides the pip button',
      not column.pip_button.get_visible())
check('the panel load brought the card back',
      webpip.card_for(host) is None
      and host.web_slot.get_child() is column)
check('a panel page refuses the card', webpip.open_card(host) is None)
webcolumn.present_web(host, BASE + '/plain.html')
check('a plain page shows the pip button again',
      column.pip_button.get_visible())

# ---------------------------------------------------------------- 4
# Expand: back into the sidebar, no reload, nothing disposed.
card = webpip.open_card(host)
LOADS['n'] = 0
expand, _close = card_buttons(card)
expand.emit('clicked')
del expand, _close
check('the column is back in the slot',
      host.web_slot.get_child() is column)
check('the sidebar shows', host.web_split.get_show_sidebar())
check('no reload happened', LOADS['n'] == 0, repr(LOADS['n']))
check('the card left the overlay and the registry',
      webpip.card_for(host) is None and card.get_parent() is None)
check('the column still lives', not column._disposed)
check('the top bars are back', column.header.get_visible())
check('card mode is off', not column.card_mode)
check('the fill is undone and the page is visible again',
      ask_true(column, '(() => {'
                       'const v = document.querySelector("video");'
                       'return !v?.hasAttribute("data-babble-pip-style")'
                       ' && getComputedStyle(document.body).visibility'
                       ' === "visible"'
                       ' && getComputedStyle(document.documentElement).overflow'
                       ' === "visible";'
                       '})()'))
check('the restored view is visible after undo',
      pump(lambda: column.view.get_opacity() == 1
            and column.view.get_sensitive()))

# ---------------------------------------------------------------- 5
# Minimize/expand cycles: the column never leaks, never rebuilds.
CYCLES = 20
leaked = False
for _cycle in range(CYCLES):
    card = webpip.open_card(host)
    if card is None or webpip.card_for(host) is not card \
            or host.web_slot.get_child() is not None:
        leaked = True
        break
    expand, _close = card_buttons(card)
    expand.emit('clicked')
    if host.web_slot.get_child() is not column \
            or webpip.card_for(host) is not None:
        leaked = True
        break
check('%d minimize/expand cycles of one column' % CYCLES, not leaked)
card = None

# ---------------------------------------------------------------- 6
# A web link while the card is out: restore, then load into it.
card = webpip.open_card(host)
check('the card is out again', webpip.card_for(host) is card)
LOADS['n'] = 0
webcolumn.present_web(host, BASE + '/plain.html')
check('the link restored the column',
      host.web_slot.get_child() is column)
model = host.overlay.observe_children()
leftover_cards = [model.get_item(i) for i in range(model.get_n_items())
                  if isinstance(model.get_item(i), webpip.WebPiPCard)]
check('no card hangs over the restored column', not leftover_cards,
      repr(leftover_cards))
check('the load went into the column', LOADS['n'] == 1, repr(LOADS['n']))
check('the card is gone', webpip.card_for(host) is None)

# ---------------------------------------------------------------- 7
# The media viewer while the card is out: restore without the sidebar.
card = webpip.open_card(host)
broken = {'type': 'image', 'url': 'http://127.0.0.1:9/nope.png'}
viewer = mediaviewer.present_media(host, [broken], 0)
check('the viewer opened', viewer is not None)
check('the card restored for the viewer',
      webpip.card_for(host) is None
      and host.web_slot.get_child() is column)
check('the sidebar did not come up for the viewer',
      not host.web_split.get_show_sidebar())
if viewer is not None:
    viewer.close()
viewer = None
check('the column survived the viewer', not column._disposed)

# ---------------------------------------------------------------- 8
# Close: the settled page goes down the immediate path, the still-
# loading one down the deferred path, with the card staying mapped
# while the page settles.
card = webpip.open_card(host)
webpip.close_card(card)
check('the close disposed the column', column._disposed)
check('the card left the registry', webpip.card_for(host) is None)
check('the card left the overlay',
      pump(lambda: card.get_parent() is None, 8),
      repr(card.get_parent()))
check('the sidebar hid over the empty slot',
      pump(lambda: not host.web_split.get_show_sidebar(), 8))
column = None
card = None
check('the closed column is freed',
      pump(lambda: (gc.collect(), ref() is None)[1], 15))

# The deferred path: a page that is still loading when the card closes
# keeps the card visible until its teardown settles.
column = open_column(BASE + '/slow.html', wait=False)
ref = column.weak_ref()
LOADS['n'] = 0
counting_load(column)
card = webpip.open_card(host)
webpip.close_card(card)
check('the deferred close keeps the card mapped',
      card.get_parent() is host.overlay
      and column.get_parent() is card._viewport.get_child())
check('the deferred close runs the column\'s grace timer',
      bool(column._teardown_timer))
check('the deferred close drops the card when the page settles',
      pump(lambda: (gc.collect(),
                    card.get_parent() is None
                    and not column._teardown_timer)[1], 10))
check('the sidebar hid over the empty slot (deferred)',
      not host.web_split.get_show_sidebar())
check('the deferred close never reloaded',
      LOADS['n'] == 0, repr(LOADS['n']))
column = None
card = None
check('the deferred column is freed',
      pump(lambda: (gc.collect(), ref() is None)[1], 15))

# ---------------------------------------------------------------- 9
# Routing: Ctrl+W (forward_action 'close') in card mode closes the
# card, never the window; focus decides. OSD focus counts as in-card.
column = open_column(BASE + '/plain.html')
ref = column.weak_ref()
card = webpip.open_card(host)
expand = card.expand_button
check('the OSD expand button holds focus',
      pump(lambda: host.get_focus() is expand or expand.has_focus(), 5))
check('forward_action close with OSD focus',
      webcolumn.forward_action(host, 'close'))
check('OSD-focused close disposed the card',
      webpip.card_for(host) is None and column._disposed)
expand = card = None
column = None
check('the OSD-routed column is freed',
      pump(lambda: (gc.collect(), ref() is None)[1], 15))

column = open_column(BASE + '/plain.html')
ref = column.weak_ref()
card = webpip.open_card(host)
column.view.grab_focus()
pump(lambda: host.get_focus() is not None
     and host.get_focus().is_ancestor(column), 5)
check('forward_action close in card mode returns True',
      webcolumn.forward_action(host, 'close'))
check('forward_action closed the card',
      webpip.card_for(host) is None and column._disposed)
card = None
column = None
check('the routed column is freed',
      pump(lambda: (gc.collect(), ref() is None)[1], 15))

# Escape with the OSD focused: the card's own shortcut closes it.
column = open_column(BASE + '/plain.html')
ref = column.weak_ref()
card = webpip.open_card(host)
escape = next(c for c in card.observe_controllers()
              if isinstance(c, Gtk.ShortcutController)).get_item(0)
escape.get_action().activate(Gtk.ShortcutActionFlags.EXCLUSIVE, card, None)
check('Escape on the card closes it',
      escape.get_trigger().to_string() == 'Escape'
      and webpip.card_for(host) is None and column._disposed)
escape = card = None
column = None
check('the Escape-closed column is freed',
      pump(lambda: (gc.collect(), ref() is None)[1], 15))

# The fullscreen denial, on a fresh column.
column = open_column(BASE + '/plain.html')
ref = column.weak_ref()
card = webpip.open_card(host)
check('fullscreen is denied in card mode',
      column._on_fullscreen(None) is True)
webpip.restore(card)
webpip.close_card(card)
check('a close after restore is a no-op',
      not column._disposed and host.web_slot.get_child() is column)
check('fullscreen works in sidebar mode',
      column._on_fullscreen(None) is False)

# The window's own close-request with the card out closes the card and
# disposes the column: the last case, because the window goes with it.
card = webpip.open_card(host)
host.emit('close-request')
check('the window close disposed the card',
      webpip.card_for(host) is None and column._disposed
      and card._closed)
# The window itself is going away now; the card leaves it with the
# window, so overlay and freed checks mean nothing past this point.
card = None
column = None

server.shutdown()
if failures:
    print('\n%d web pip check(s) failed: %s' % (len(failures),
                                                ', '.join(failures)))
    sys.exit(1)
print('\nall web pip checks passed')
