#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regressions for the timeline's list plumbing: a new post must
not make the list rebuild every row, dropped rows and pages must be
freed, and a body that has never been allocated must measure the height
of its text, not of one character per line. Exits 77 (skip) without a
display."""

import gc
import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.i18n import gettext as _  # noqa: E402
from src import mediaviewer, notifications, profile, timeline  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def status(number, text='post', thread=None):
    data = {'id': '%05d' % number, 'content': '<p>%s</p>' % text,
            'account': {'acct': 'user%d' % number, 'id': str(number)},
            'created_at': '2026-01-01T00:00:00.000Z'}
    if thread:
        data['thread'] = thread
    return data


def freed(ref):
    gc.collect()
    return ref() is None


Adw.init()

# A streamed post is one inserted run; every other run object survives,
# so the list view keeps the widgets bound to them.
model = timeline.TimelineModel()
model.append_old([status(n) for n in range(30, 0, -1)])
runs = timeline.RunModel(model)
kept = [runs.get_item(i) for i in range(runs.get_n_items())]
changes = []
runs.connect('items-changed', lambda _m, *change: changes.append(change))
model.insert_new([status(31)])
check('one new post is one inserted run', changes == [(0, 0, 1)])
check('existing runs are kept',
      all(runs.get_item(i + 1) is run for i, run in enumerate(kept)))
changes.clear()
model.remove_status('00015')
check('a deletion removes one run', changes == [(16, 1, 0)])
changes.clear()
check('an edit changes the post in place',
      model.replace_status({'id': '00020', 'content': '<p>edited</p>'})
      and changes == [])
carousel = timeline.BoostCarouselItem([status(5)], [])
model._store.insert(0, carousel)
changes.clear()
carousel.set_boosts([status(6)])
check('the boosts carousel is updated in place',
      changes == [] and model.get_item(0) is carousel
      and carousel.boosts[0]['id'] == '00006')

# Trimming keeps the newest posts and pages from the oldest one left,
# so infinite scroll can fetch the dropped posts again.
long = timeline.TimelineModel()
long.append_old([status(n) for n in range(30, 0, -1)])
long.exhausted = True
long_runs = timeline.RunModel(long)
check('trim drops the oldest posts', long.trim(10)
      and long.get_n_items() == 10 and long_runs.get_n_items() == 10
      and long.get_item(0).status_id == '00030')
check('trim pages from the oldest post left',
      long.oldest_id == '00021' and not long.exhausted)
check('dropped posts can be loaded again',
      long.append_old([status(n) for n in range(20, 15, -1)])
      and long.get_n_items() == 15)
check('trim within the limit does nothing', not long.trim(40))
boosted = timeline.TimelineModel()
original = status(9)
boost = dict(status(8), reblog=original)
boosted.append_old([status(10), original, status(7), boost])
boosted.trim(3)
check('a post still shown stays seen after its boost is dropped',
      not boosted.insert_new([status(9)]))

# Same-thread posts by one author still group into one run.
threaded = timeline.TimelineModel()
threaded.append_old([status(3), status(2, thread='t'), status(1, thread='t')])
for item in (threaded.get_item(1), threaded.get_item(2)):
    item.status['account']['acct'] = 'same'
grouped = timeline.RunModel(threaded)
check('same-thread posts form one run',
      [len(grouped.get_item(i).items)
       for i in range(grouped.get_n_items())] == [1, 2])


class _Client:
    token = None
    instance_info = None


class _Source:
    paginated = True

    def fetch(self, _params, _callback):
        pass


# Rows, their text views and the page are freed once dropped: signal
# handlers into a widget's own methods must not keep it alive.
row = timeline.StatusRow()
row.bind(timeline.StatusItem(status(1, 'see <a href="https://e.org">'
                                       'e.org</a>')), _Client())
row_ref, body_ref = row.weak_ref(), row.content.weak_ref()
del row
check('a dropped status row is freed', freed(row_ref))
check('its text view is freed', freed(body_ref))

page = timeline.TimelinePage(None, source=_Source())
page_ref, model_ref = page.weak_ref(), page.model.weak_ref()
del page
check('a dropped timeline page is freed', freed(page_ref))
check('its model is freed', freed(model_ref))


class _ApiClient:
    """Signed-in client whose requests never complete; *request_finish*
    hands back the payload a test feeds a page's callbacks."""
    token = 'token'
    instance_info = None
    instance = 'https://example.org'

    def request_async(self, *_args, **_kwargs):
        pass

    def request_finish(self, result):
        return result

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def account(number, **extra):
    return dict({'id': str(number), 'acct': 'user%d' % number,
                 'display_name': 'User %d' % number}, **extra)


# Profile and notifications pages are freed once dropped, with the
# content they build after their requests load.
api = _ApiClient()
field = {'name': 'site', 'value': '<a href="https://e.org">e.org</a>'}
page = profile.ProfilePage(api, account(
    1, note='<p>hi <a href="https://example.org/tags/t">#t</a></p>',
    fields=[field] * 6, moved=account(2)), viewer_id='99')
page._on_familiar(api, [{'accounts': [account(3), account(4)]}])
page._on_featured_tags(api, [{'name': 'tag', 'statuses_count': 3}])
page._on_endorsements(api, [account(n) for n in range(5, 10)])
page._open_image_on_click(page.avatar, 'https://e.org/a.png', None)
page._relationship_known = True
page.set_relationship({'id': '1', 'following': True, 'note': 'note'})
page_ref = page.weak_ref()
del page
check('a dropped profile page is freed', freed(page_ref))

view = Adw.NavigationView()
dialog = profile.ProfileDialog(
    view, profile.ProfilePage(api, account(1), viewer_id='99'), 'User 1')
dialog_ref = dialog.weak_ref()
del dialog
check('a dropped profile sheet is freed', freed(dialog_ref))

dialog = profile.PrivateNoteDialog('@user1', 'note', lambda _text: None)
dialog_ref = dialog.weak_ref()
del dialog
check('a dropped private note dialog is freed', freed(dialog_ref))

actor = account(3)
row = notifications.NotificationRow()
row.bind(notifications.NotificationItem(
    {'id': '1', 'type': 'follow_request', 'account': actor,
     'created_at': '2026-10-05T10:10:00.000Z'}), api)
row_ref = row.weak_ref()
del row
check('a dropped notification row is freed', freed(row_ref))

model = notifications.NotificationModel()
model.append_page([{'id': '1', 'type': 'mention', 'account': actor,
                    'created_at': '2026-10-05T10:10:00.000Z',
                    'status': status(1, 'hello')}])
page = notifications.NotificationsPage(api, model=model)
page_ref, model_ref = page.weak_ref(), model.weak_ref()
del page, model
check('a dropped notifications page is freed', freed(page_ref))
check('its notification model is freed', freed(model_ref))

# Before its first allocation a wrapped body measures its text at the
# width it is offered; Gtk.TextView alone would wrap it at one pixel.
window = Gtk.Window()
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
window.set_child(box)
body = timeline.StatusBody()
body.set_segments(timeline.parse_content('<p>%s</p>' % ('word ' * 40)))
box.append(body)
natural = box.measure(Gtk.Orientation.VERTICAL, 600).natural
check('an unallocated body is not one character per line',
      0 < natural < 200)


def settle(ready=lambda: True, seconds=5.0, quiet=0.1):
    """Run the main loop until *ready* holds and nothing has been
    dispatched for *quiet* seconds: layout runs on frame ticks, so an
    empty queue between two frames is not the end of it."""
    context = GLib.MainContext.default()
    now = GLib.get_monotonic_time
    deadline = now() + int(seconds * 1000000)
    last = now()
    while now() < deadline:
        if context.iteration(False):
            last = now()
        elif ready() and now() - last >= quiet * 1000000:
            return
        else:
            GLib.usleep(2000)


# A shell's placeholder height must not outlive it: a short post bound
# into a slot sized for a tall one would sit above a blank gap.
box.remove(body)
window.set_default_size(660, 900)
window.present()
typical = timeline.StatusRunRow._typical_height
timeline.StatusRunRow._typical_height = 700
shell = timeline.StatusRunRow()
shell.bind(grouped.get_item(0), _Client())
box.append(shell)
settle(lambda: shell.get_mapped() and shell.get_height() > 0)
check('a shown run is as tall as its card, not its placeholder',
      shell.get_mapped() and shell.get_height() < 400)
timeline.StatusRunRow._typical_height = typical
box.remove(shell)

# A height remembered for an off-screen run must not stay as a minimum
# once the run is shown again: after the column widens, a post that got
# shorter would otherwise keep a blank gap every time it comes back.
def long_run():
    model = timeline.TimelineModel()
    model.append_old([status(1, 'word ' * 120)])
    return timeline.RunModel(model).get_item(0)


def clamped_shell(width):
    clamp = Adw.Clamp(maximum_size=width, tightening_threshold=width)
    shell = timeline.StatusRunRow()
    shell.bind(long_run(), _Client())
    clamp.set_child(shell)
    box.append(clamp)
    return clamp, shell


clamp, shell = clamped_shell(360)
settle(lambda: shell.get_mapped() and shell.get_height() > 64)
narrow = shell.get_height()
clamp.set_maximum_size(640)
clamp.set_tightening_threshold(640)
for visible in (False, True):
    shell.set_visible(visible)
    settle()
fresh_clamp, fresh = clamped_shell(640)
settle(lambda: fresh.get_mapped() and fresh.get_height() > 64)
# A re-shown shell can settle a few pixels off a fresh one (it did so
# before the height cache existed); a stuck height is the whole
# narrow-column difference.
check('a widened post shown again has no blank gap',
      fresh.get_height() < narrow - 32
      and abs(shell.get_height() - fresh.get_height()) < 16)
box.remove(clamp)
box.remove(fresh_clamp)

# The pre-allocation estimate is plain text; paragraph gaps and inline
# images change the real height, which must replace it once allocated.
#
# What this guards is that the estimate was replaced at all: 64 here,
# against a real height of roughly 255. The tolerance is relative rather
# than a fixed number of pixels because the real height comes from the
# font stack, which is not the same everywhere -- a desktop resolves sans
# to a static Noto Sans, a container to the variable font, and the two
# lay this body out 255 and 285 pixels tall. Pinning exact pixels would
# fail a correct build on whichever machine disagrees.
body = timeline.StatusBody()
body.set_segments(timeline.parse_content(
    ''.join('<p>paragraph %d</p>' % n for n in range(8))))
box.append(body)
settle(lambda: body.get_width() > 0)
real = Gtk.TextView.do_measure(body, Gtk.Orientation.VERTICAL,
                               body.get_width())[1]
check('an allocated body is as tall as its text',
      real > 0 and abs(body.get_height() - real) <= real * 0.1)
box.remove(body)


def drag_sources(widget):
    """Every Gtk.DragSource on *widget* and its descendants."""
    found = []
    controllers = widget.observe_controllers()
    for position in range(controllers.get_n_items()):
        controller = controllers.get_item(position)
        if isinstance(controller, Gtk.DragSource):
            found.append(controller)
    del controllers
    child = widget.get_first_child()
    while child is not None:
        found += drag_sources(child)
        child = child.get_next_sibling()
    return found


# Dragging a post or one of its pictures out, or a picture out of the
# viewer, must not keep them alive: the drag sources' handlers are weak
# and the drag icons do not hold their widgets.
texture = Gdk.MemoryTexture.new(4, 4, Gdk.MemoryFormat.R8G8B8A8,
                                GLib.Bytes.new(b'\x80' * 64), 16)
picture_url = 'https://example.org/media/a.png'
timeline._TextureCache._textures[
    (picture_url, (timeline.COLUMN_WIDTH, 260))] = texture
pictured = dict(status(2, 'pictured'), url='https://example.org/@user2/2',
                media_attachments=[{'id': '1', 'type': 'image',
                                    'url': picture_url}])
row = timeline.StatusRow()
row.bind(timeline.StatusItem(pictured), _Client())
box.append(row)
settle(lambda row=row: row.get_mapped() and row.avatar.get_mapped())
prepared = [source.emit('prepare', 1.0, 1.0) for source in drag_sources(row)]
check('a post and its picture can be dragged out',
      len(prepared) == 2 and all(prepared))
del prepared
box.remove(row)
row_ref = row.weak_ref()
del row
settle()
check('a dragged status row is freed', freed(row_ref))

# Hosted in an Adw window, like the app's: over a plain Gtk.Window the
# dialog floats as a window of its own, and force_close() did not emit
# 'closed' there.
host = Adw.Window(content=Gtk.Box())
host.present()
viewer = mediaviewer.MediaViewer(
    host.get_content(),
    [{'type': 'unknown', 'url': 'https://example.org/media/a.bin'}])
viewer.present(host)
settle()
viewer._on_texture(texture, None)
check('Copy Image accepts the displayed texture', viewer.copy_image())
check('Copy Image puts a texture on the clipboard',
      host.get_display().get_clipboard().get_formats()
      .contain_gtype(Gdk.Texture))
prepared = [viewer.picture_drag.emit('prepare', 1.0, 1.0),
            viewer.media_drag.emit('prepare', 1.0, 1.0)]
check('the viewer can drag its picture and its media out', all(prepared))
del prepared
system_state = mediaviewer._system_state
mediaviewer._system_state = lambda name: name == 'metered'
viewer.attachments = [{'type': 'video',
                       'url': 'https://example.org/media/a.mp4'}]
viewer.show_index(0)
check('metered video waits for an explicit load',
      viewer.stack.get_visible_child_name() == 'deferred'
      and viewer.load_button.get_label() == _('Load _Video'))
mediaviewer._system_state = system_state
viewer.force_close()
settle(lambda viewer=viewer: viewer._closed)
check('a closed media viewer ran its teardown', viewer._closed)
viewer_ref = viewer.weak_ref()
del viewer
check('a closed media viewer is freed', freed(viewer_ref))
host.destroy()
timeline._TextureCache._textures.clear()

# Weak hookups must survive PyGObject dropping and re-creating the Python
# wrapper of a widget that only GTK holds.
held = timeline.TimelinePage(None, source=_Source())
box.append(held)
held._pill_count = 5
held.new_posts_revealer.set_reveal_child(True)
del held
gc.collect()
held = box.get_last_child()
held.new_pill.emit('clicked')
check('a page held only by GTK still handles its signals',
      held._pill_count == 0
      and not held.new_posts_revealer.get_reveal_child())

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all timeline memory checks passed')
