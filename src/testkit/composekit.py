#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixtures shared by the composer test parts.

The part imports this after it registered the resource bundle and called
Adw.init(); importing it builds the one stub client, the composer view
the scenarios drive, and the harness state they share. The parts are
separate processes, so every fixture lives here and nothing is left in a
part's module globals but the scenario itself.
"""

import inspect
import os
import tempfile

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

import src.portals as portals_module
from src import appsettings, feedback
from src.api import media as api_media, statuses as api_statuses
from src.compose import BabbleComposeView
from src.composedialog import ComposeDialog

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' :: %s' % detail) if detail else ''))

SCREENSHOT = 'org.freedesktop.portal.Screenshot'
#: Portal interface -> version the stub portal_version answers.
portal_versions = {SCREENSHOT: 1}
#: What session_bus hands over; None plays a missing session bus.
session_bus = [object()]
#: (interface, method, signature, args, options) of each portal call.
portal_calls = []
#: (code, results) answered to the next calls, in order; with none
#: left, a call's on_response is held in held_responses.
portal_answers = []
held_responses = []

def stub_portal_version(interface, callback):
    callback(portal_versions.get(interface, 0))


def stub_session_bus(callback):
    callback(session_bus[0])


def stub_portal_request(bus, interface, method, signature, args, options,
                        on_response):
    # The real helper adds the handle_token and sends this very tuple.
    GLib.Variant(signature, (*args, dict(
        options, handle_token=GLib.Variant('s', 'babble_test'))))
    portal_calls.append((interface, method, signature, args,
                         {key: value.unpack()
                          for key, value in options.items()}))
    if portal_answers:
        on_response(*portal_answers.pop(0))
    else:
        held_responses.append(on_response)


portals_module.portal_version = stub_portal_version
portals_module.session_bus = stub_session_bus
portals_module.portal_request = stub_portal_request

PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d494844520000000100000001080600000'
    '01f15c4890000000d4944415478da6360000002000100ffff030000060005'
    '57bfabd40000000049454e44ae426082')

posts = []
uploads = []
polls = []
searches = []
edits = []
#: What the stub answers for /api/v2/search; a test may point it at any
#: other payload (e.g. statuses for quote resolution). None means the
#: default hashtag searches.
SEARCH_ANSWER = None

SEARCH_ACCOUNTS = [
    {'id': '1', 'acct': 'alice', 'username': 'alice',
     'display_name': 'Alice :blobcat:'},
    {'id': '2', 'acct': 'bob@remote.example', 'username': 'bob',
     'display_name': ''},
]

# What the stub answers by path: the mention completer's account search,
# the hashtag completer's search, and the three lists behind its pool.
HASHTAG_ANSWER = []
POOL_ANSWERS = {'/featured_tags/suggestions': [],
                '/featured_tags': [],
                '/followed_tags': []}
#: Paths that answer with an error, and paths whose reply a test holds
#: back until release_requests() (the reply of a slow server).
FAIL_PATHS = set()
HOLD_PATHS = set()
held_requests = []

class StubMediaResult:
    """Results are opaque; the view only passes them to *_finish."""

    #: Set by a test to make the instance answer an undecoded upload.
    needs_processing = False
    #: Set by a test to answer with a cancellation instead of data.
    cancelled = False
    #: Set by a test to hold status replies until it releases them.
    defer_status = False
    #: Replies withheld by defer_status, released by release_status().
    held = []
    #: The request this reply answers; request_finish routes on it.
    path = '/accounts/search'


class StubClient:
    """Minimal stand-in matching the MastodonClient surface used by
    BabbleComposeView (post and upload, routed from `src.api`), with counters."""

    def __init__(self):
        self.token = None

    def is_supported(self, _min_version, when_unknown=False):
        # A current server: every version gate is open.
        return True

    def media_size_limits(self):
        # No limit advertised, which is what most of these tests want:
        # an instance that says nothing must not refuse anything.
        return None, None

    def post_async(self, text, params=None, callback=None,
                          cancellable=None):
        form = {'status': text}
        if params:
            form.update(params)
        posts.append(form)
        def cb(source, result, _user_data=None):
            callback(source, result)
        GLib.idle_add(lambda *_: (cb(self, StubMediaResult()),
                                  GLib.SOURCE_REMOVE)[1])
        return None

    def post_finish(self, result):
        return {'id': 'invented', 'content': '<p>x</p>'}

    def edit_async(self, status_id, text, params=None, callback=None,
                          cancellable=None):
        # Edits carry their target id, unlike posts.
        form = {'status': text}
        if params:
            form.update(params)
        edits.append((status_id, form))

        def cb(source, result, _user_data=None):
            callback(source, result)
        GLib.idle_add(lambda *_: (cb(self, StubMediaResult()),
                                  GLib.SOURCE_REMOVE)[1])
        return None

    def edit_finish(self, result):
        return {'id': 'invented', 'content': '<p>x</p>'}

    def upload_async(self, file_bytes, filename, content_type,
                           description=None, callback=None,
                           cancellable=None):
        # upload_async takes the payload as bytes or as a
        # GLib.Bytes, and the composer hands over whichever it has.
        size = (file_bytes.get_size() if hasattr(file_bytes, 'get_size')
                else len(file_bytes))
        uploads.append({'bytes': size, 'filename': filename,
                        'content_type': content_type,
                        'description': description,
                        'id': '%d' % (9900 + len(uploads) + 1)})
        index = len(uploads) - 1

        def cb(source, result, _user_data=None):
            media = {'id': uploads[index]['id']}
            if result.needs_processing:
                # The instance accepted the bytes but has not decoded
                # them yet: no url, so the composer must poll.
                media['url'] = None
            else:
                media['url'] = 'https://example.social/media/%s.png' % index
            result._media = media  # type: ignore[attr-defined]
            callback(source, result)

        GLib.idle_add(lambda *_: (cb(self, StubMediaResult()),
                                  GLib.SOURCE_REMOVE)[1])
        return None

    def upload_finish(self, result):
        return result._media

    def status_async(self, media_id, callback=None, cancellable=None):
        polls.append(media_id)

        def cb(source, result, _user_data=None):
            # First poll still reports no url, as an instance mid-transcode
            # would; the second one is ready.
            ready = polls.count(media_id) > 1
            result._media = {  # type: ignore[attr-defined]
                'id': media_id,
                'url': ('https://example.social/media/%s.png' % media_id
                        if ready else None)}
            callback(source, result)

        result = StubMediaResult()
        if StubMediaResult.defer_status:
            # Hold the reply so a test can order it against a reset; a
            # reply that lands after the draft is gone is the race.
            StubMediaResult.held.append((self, result, cb))
        else:
            GLib.idle_add(lambda *_: (cb(self, result),
                                      GLib.SOURCE_REMOVE)[1])
        return None

    @staticmethod
    def release_status():
        """Answer every held status reply."""
        held, StubMediaResult.held = StubMediaResult.held, []
        for client, result, cb in held:
            cb(client, result)

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        # The completers' searches go through here, and nothing else.
        searches.append((method, path, dict(params or {})))
        result = StubMediaResult()
        result.path = path
        if path in HOLD_PATHS:
            held_requests.append((self, result, callback))
        else:
            GLib.idle_add(lambda *_: (callback(self, result),
                                      GLib.SOURCE_REMOVE)[1])
        return None

    @staticmethod
    def release_requests():
        """Answer every request a test held back with HOLD_PATHS."""
        held, held_requests[:] = list(held_requests), []
        for client, result, callback in held:
            callback(client, result)

    def request_finish(self, result):
        if result.path in FAIL_PATHS:
            raise GLib.Error('Stub failure', Gio.io_error_quark(),
                             Gio.IOErrorEnum.FAILED)
        if result.path == '/api/v2/search':
            if SEARCH_ANSWER is not None:
                return SEARCH_ANSWER
            return {'hashtags': HASHTAG_ANSWER}
        if result.path in POOL_ANSWERS:
            return POOL_ANSWERS[result.path]
        return SEARCH_ACCOUNTS

    def status_finish(self, result):
        if result.cancelled:
            # What a real cancellation looks like to the caller: a
            # GLib.Error in the Gio.IOErrorEnum.CANCELLED domain, not a
            # server error, so retrying it would be nonsense.
            raise GLib.Error('Operation was cancelled',
                             Gio.io_error_quark(),
                             Gio.IOErrorEnum.CANCELLED)
        return result._media

def _route(module, name):
    """The composer calls `src.api` functions with the client first. This
    suite stubs the network, so those functions are replaced by ones that
    hand the call to the stub's method of the same name (the old client
    methods this stub was written for)."""
    setattr(module, name,
            lambda client, *args, **kwargs: getattr(client, name)(
                *args, **kwargs))


for _name in ('post_async', 'post_finish', 'edit_async', 'edit_finish'):
    _route(api_statuses, _name)
for _name in ('upload_async', 'upload_finish', 'status_async',
              'status_finish'):
    _route(api_media, _name)

class StubApp(Gtk.Application):
    """The default application, recording the logout and suspend
    inhibitions the composer takes while posting."""

    def __init__(self):
        super().__init__(application_id='io.github.mauriciobc.BabbleTest',
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.inhibited = []
        self.held = set()
        self.released = []

    def inhibit(self, window, flags, reason):
        cookie = len(self.inhibited) + 1
        self.inhibited.append((window, flags, reason, cookie))
        self.held.add(cookie)
        return cookie

    def uninhibit(self, cookie):
        self.released.append(cookie)
        self.held.discard(cookie)


stub_app = StubApp()
Gio.Application.set_default(stub_app)
INHIBIT_FLAGS = (Gtk.ApplicationInhibitFlags.LOGOUT
                 | Gtk.ApplicationInhibitFlags.SUSPEND)

announced = []
feedback.announce = lambda widget, text, *a: announced.append(text)

# Network-free widget exercise: no display timing needed beyond pumps.
client = StubClient()
view = BabbleComposeView(client)

window = Gtk.Window()
window.set_title('compose test')
window.set_child(view)
window.set_default_size(420, 720)
window.present()

# is covered by the dialog). Adw.ToastOverlay has no get_toasts(), so the
# view's toast helper is wrapped instead.
toast_titles = []
_original_add_toast = view._add_toast


def _capture_toast(toast):
    toast_titles.append(toast.get_title())
    _original_add_toast(toast)


view._add_toast = _capture_toast


def capture_toasts(target):
    """Wrap another view's toast helper, so collect_toasts() sees it too."""
    original = target._add_toast

    def capture(toast):
        toast_titles.append(toast.get_title())
        original(toast)
    target._add_toast = capture


def collect_toasts():
    """Titles raised since the last call."""
    texts = list(toast_titles)
    toast_titles.clear()
    return texts

# The success toast after the dialog closes goes through the window.
window.show_toast = lambda text: toast_titles.append(text)  # type: ignore

def pump(millis=100):
    deadline = GLib.get_monotonic_time() + millis * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        ctx.iteration(False)
    while ctx.iteration(False):
        pass

def settle_until(deadline_s, condition):
    """Pump until *condition* holds, or the deadline passes.

    Used wherever a widget's own frame has to land before it can be
    inspected: a popover's visibility, a dialog's 'closed' signal. A
    fixed pump is either too long on a fast machine or too short on a
    loaded one, and this test file has both kinds of wait.
    """
    end = GLib.get_monotonic_time() + int(deadline_s * GLib.USEC_PER_SEC)
    while GLib.get_monotonic_time() < end and not condition():
        pump(50)
    return condition()


def set_text(text):
    view.buffer.set_text(text)
    view.on_draft_changed()

# How long an async clipboard read may take before the paste checks give
# up. Generous on purpose: this is a ceiling, not a target, and a loaded
# machine must not fail the test for being slow.
PASTE_WAIT_S = 10.0

tmp_png = os.path.join('/tmp', 'babble-test.png')
with open(tmp_png, 'wb') as f:
    f.write(PNG_BYTES)

def new_dialog():
    """The dialog surface the dialog scenarios drive."""
    app_window = Adw.ApplicationWindow(default_width=1000,
                                       default_height=900)
    app_window.present()
    dialog = ComposeDialog(client)
    dialog.present(app_window)
    return app_window, dialog

local_settings = appsettings.settings()

completer = view.mention_completer
NO_MODS = Gdk.ModifierType(0)

def press(keyval, state=NO_MODS):
    return completer._on_key_pressed(None, keyval, 0, state)


def settle():
    """Run the loop until the completer's debounced search has been
    answered: a fixed wait is too short on a loaded machine."""
    deadline = GLib.get_monotonic_time() + 3 * 1000 * 1000
    while (completer._search_id or completer._cancellable is not None) \
            and GLib.get_monotonic_time() < deadline:
        pump(20)
    pump(50)


def type_text(text, cursor=None):
    view.buffer.set_text(text)
    position = (view.buffer.get_end_iter() if cursor is None
                else view.buffer.get_iter_at_offset(cursor))
    view.buffer.place_cursor(position)
    settle()

from src import completer as completer_module  # noqa: E402

base_source = inspect.getsource(completer_module.TokenCompleter)

def wait_until(condition, seconds=3):
    deadline = GLib.get_monotonic_time() + seconds * 1000 * 1000
    while not condition() and GLib.get_monotonic_time() < deadline:
        pump(20)
    pump(50)

hcomp = view.hashtag_completer
LINUX = {'name': 'linux', 'history': [{'uses': '30', 'accounts': '9'},
                                      {'uses': '5', 'accounts': '2'}]}
MINT = {'name': 'linuxmint', 'history': [{'uses': '3', 'accounts': '1'}]}

def hpress(keyval, state=NO_MODS):
    return hcomp._on_key_pressed(None, keyval, 0, state)


def hsettle():
    deadline = GLib.get_monotonic_time() + 3 * 1000 * 1000
    while (hcomp._search_id or hcomp._cancellable is not None) \
            and GLib.get_monotonic_time() < deadline:
        pump(20)
    pump(50)


def htype(text, cursor=None):
    view.buffer.set_text(text)
    view.buffer.place_cursor(view.buffer.get_end_iter() if cursor is None
                             else view.buffer.get_iter_at_offset(cursor))
    hsettle()


def hnames():
    found = []
    while (row := hcomp.list_box.get_row_at_index(len(found))) is not None:
        found.append(row.tag_name)
    return found


def hselected():
    row = hcomp.list_box.get_selected_row()
    return None if row is None else row.tag_name


def hashtag_searches():
    return [entry for entry in searches if entry[1] == '/api/v2/search']


def cursor_offset():
    return view.buffer.get_iter_at_mark(
        view.buffer.get_insert()).get_offset()

media_dir = tempfile.mkdtemp(prefix='babble-test-media-')


def media_file(name, data=PNG_BYTES):
    path = os.path.join(media_dir, name)
    with open(path, 'wb') as f:
        f.write(data)
    return path

def file_list(paths):
    return Gdk.FileList.new_from_list([Gio.File.new_for_path(p)
                                       for p in paths])


def drop(value):
    return view.drop_target.emit('drop', value, 0.0, 0.0)
