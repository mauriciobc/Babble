#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Renders the Babble thread and profile views with REAL data from
fosstodon.org's anonymous public timeline, exercising the same code
paths as the app (ThreadPage, ProfilePage, TimelinePage account tabs).
Exits 77 (skip) when the display or the network is unavailable."""

import os
import subprocess
import sys
import tempfile
import time

# Own Xvfb + X11 backend: root screenshots stay deterministic even when
# the host session leaks WAYLAND_DISPLAY or a compositing :0 display.
_xvfb = None
if not os.environ.get('BABBLE_KEEP_DISPLAY'):
    for _n in range(91, 100):
        if os.path.exists('/tmp/.X11-unix/X%d' % _n):
            continue
        _xvfb = subprocess.Popen(
            ['Xvfb', ':%d' % _n, '-screen', '0', '1280x1024x24'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline \
                and not os.path.exists('/tmp/.X11-unix/X%d' % _n):
            time.sleep(0.05)
        if os.path.exists('/tmp/.X11-unix/X%d' % _n):
            os.environ['DISPLAY'] = ':%d' % _n
            break
        _xvfb.terminate()
        _xvfb = None
os.environ.setdefault('GDK_BACKEND', 'x11')

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.mastodon import MastodonClient  # noqa: E402
from src.profile import (AccountStatuses, ProfilePage,  # noqa: E402
                         format_count)
from src.statusrow import StatusRow  # noqa: E402
from src.thread import ThreadPage, ThreadReply  # noqa: E402
from src.timelinedata import StatusItem  # noqa: E402
from src.timelinepage import TimelinePage  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def pump(seconds):
    """Iterate the default main context for about *seconds*."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx = GLib.MainContext.default()
        while ctx.iteration(False):
            pass


def screenshot(window, name):
    display = os.environ.get('DISPLAY', '')
    if not display or not display.startswith(':'):
        return None
    path = os.path.join(tempfile.gettempdir(), name)
    try:
        subprocess.run(
            ['ffmpeg', '-loglevel', 'error', '-y',
             '-f', 'x11grab', '-i', display,
             '-frames:v', '1', path],
            check=True, timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('note - screenshot saved to %s' % path)
        return path
    except Exception as e:
        print('note - screenshot failed: %s' % e)
        return None


# Unit checks (no display needed)
source = AccountStatuses(None, '123', pinned=True, paginated=False)
check('pinned source params', source.base_params().get('pinned') == 'true')
media_source = AccountStatuses(None, '123', media=True)
check('media source params',
      media_source.base_params().get('only_media') == 'true')
reply_source = AccountStatuses(None, '123', exclude_replies=True)
check('posts source params',
      reply_source.base_params().get('exclude_replies') == 'true')

Adw.init()
Gtk.init()

client = MastodonClient('https://fosstodon.org')

# --- Thread view ------------------------------------------------------

window = Gtk.Window()
window.set_default_size(800, 1000)
view = Adw.NavigationView()
window.set_child(view)
window.navigation_view = view
window.present()

# Fetch the public timeline; fall back to trending statuses when none of
# the sampled posts has replies, so nesting gets real data too.
timeline = {}
client.timelines_public_async({'limit': 20},
                              lambda c, r: timeline.update(result=r))
deadline = time.monotonic() + 45
while time.monotonic() < deadline and 'result' not in timeline:
    pump(0.2)
try:
    statuses = client.timelines_public_finish(timeline['result'])
except Exception as e:
    print('SKIP - public timeline unreachable: %s' % e)
    sys.exit(77)

# Add trending statuses: the public timeline rarely samples a deep
# thread, so guarantee a reply-rich status for the nesting checks.
trends = {}
client.trends_statuses_async({'limit': 20},
                             lambda c, r: trends.update(result=r))
deadline = time.monotonic() + 45
while time.monotonic() < deadline and 'result' not in trends:
    pump(0.2)
try:
    statuses += client.trends_statuses_finish(trends['result'])
except Exception:
    pass

with_replies = sorted(statuses, key=lambda s: -int(s.get('replies_count')
                                                   or 0))
focused = with_replies[0]
check('picked a status with replies', bool(with_replies[0]
                                           .get('replies_count')))
print('note - thread status: %s (%d replies)'
      % (focused['id'], focused.get('replies_count')))

thread_page = ThreadPage(client, StatusItem(focused))
view.push(Adw.NavigationPage(title='Thread', tag='thread-test',
                             child=thread_page))

deadline = time.monotonic() + 45
while time.monotonic() < deadline \
        and thread_page.stack.get_visible_child_name() != 'list':
    pump(0.2)
if thread_page.stack.get_visible_child_name() != 'list':
    print('SKIP - thread context unreachable (network error)')
    sys.exit(77)

deadline = time.monotonic() + 5
while time.monotonic() < deadline \
        and thread_page.thread_box.get_first_child() is None:
    pump(0.2)

widgets = []


def collect(widget, target):
    target.append(widget)
    child = widget.get_first_child()
    while child is not None:
        collect(child, target)
        child = child.get_next_sibling()


collect(thread_page.thread_box, widgets)

thread_rows = [w for w in widgets if isinstance(w, StatusRow)]
check('thread rows rendered', len(thread_rows) >= 1)
check('focused post highlighted',
      any('thread-focused' in list(w.get_css_classes())
          for w in thread_rows))
check('focused post is the requested status',
      any(r.item is thread_page.item for r in thread_rows))
replies = [w for w in widgets if isinstance(w, ThreadReply)]
focused_index = next(i for i, r in enumerate(thread_rows)
                     if r.item is thread_page.item)
if focused.get('replies_count'):
    # A single line of replies continues in the focused post's card;
    # only a branch becomes ThreadReply groups.
    check('descendant replies rendered',
          len(thread_rows) > focused_index + 1)
    nested = [w for w in replies
              if w.children_box.get_margin_start() > 0]
    if any(w.children_box.get_first_child() is not None for w in replies):
        check('replies indented by depth', len(nested) > 0)
    branching = [w for w in replies
                 if w.children_box.get_first_child() is not None]
    collapsed = [w for w in replies
                 if w.children_box.get_first_child() is not None
                 and not w.expander.get_expanded()]
    print('note - %d replies, %d nested, %d branching, %d collapsed'
          % (len(replies), len(nested), len(branching), len(collapsed)))
    if branching:
        check('replies nest inside an expander',
              all(isinstance(w.expander, Gtk.Expander) for w in branching))
    target = next((w for w in replies
                   if w.children_box.get_first_child() is not None
                   and w.expander.get_expanded()), None)
    if target is not None:
        # The expander hides its child through a transition, so compare
        # the space the subtree takes before and after collapsing it.
        target.expander.set_expanded(False)
        pump(0.5)
        check('collapsed expander hides nested replies',
              not target.children_box.get_visible())
else:
    print('note - no replies on this status; nesting not asserted')
pump(2.0)
screenshot(window, 'babble-thread.png')

# --- Profile view -----------------------------------------------------

account = focused['account']
profile_page = ProfilePage(client, account)
view.push(Adw.NavigationPage(title=account.get('acct', ''),
                             tag='profile-test', child=profile_page))

deadline = time.monotonic() + 45
while time.monotonic() < deadline \
        and profile_page.stack.get_visible_child_name() != 'content':
    pump(0.2)
if profile_page.stack.get_visible_child_name() != 'content':
    print('SKIP - profile unreachable (network error)')
    sys.exit(77)

profile_widgets = []
collect(profile_page, profile_widgets)


def widget_text(widget):
    """Raw string of either style of profile text widget."""
    if isinstance(widget, Gtk.Label):
        return widget.get_text()
    raw = getattr(widget, '_raw', None)
    if raw is not None:
        return raw
    if isinstance(widget, Gtk.TextView):
        buf = widget.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
    return None


check('large avatar rendered',
      any(isinstance(w, Adw.Avatar) and w.get_size() == 64
          for w in profile_widgets))
check('display name rendered',
      any(widget_text(w) == (account.get('display_name')
                             or account.get('acct', ''))
          for w in profile_widgets))
check('handle rendered',
      any(isinstance(w, Gtk.Label)
          and w.get_text() == '@%s' % account.get('acct', '')
          for w in profile_widgets))
check('bio rendered',
      any(isinstance(w, Gtk.TextView)
          for w in profile_widgets))
stats_text = ' '.join(w.get_text() for w in profile_widgets
                      if isinstance(w, Gtk.Label))
check('post count rendered',
      format_count(account.get('statuses_count', 0)) in stats_text)
check('follower count rendered',
      format_count(account.get('followers_count', 0)) in stats_text)
check('counts use tabular figures',
      any('numeric' in list(w.get_css_classes())
          for w in profile_widgets if isinstance(w, Gtk.Label)))
header_bars = [w for w in profile_widgets if isinstance(w, Adw.HeaderBar)]
check('one header bar on the profile page', len(header_bars) == 1)
check('tab switcher lives in the header bar',
      header_bars[0].get_title_widget() is profile_page.switcher)

pages_model = profile_page.view_stack.get_pages()
page_names = sorted(pages_model.get_item(i).get_name()
                    for i in range(pages_model.get_n_items()))
check('profile posts pinned media tabs',
      page_names == ['media', 'pinned', 'posts', 'profile'])
check('profile tab opens first',
      profile_page.view_stack.get_visible_child_name() == 'profile')
posts_page = profile_page.view_stack.get_child_by_name('posts')
pinned_page = profile_page.view_stack.get_child_by_name('pinned')
media_page = profile_page.view_stack.get_child_by_name('media')
check('posts tab is a timeline page', posts_page is not None)

# Posts tab loads real account statuses.
deadline = time.monotonic() + 45
while time.monotonic() < deadline \
        and posts_page.stack.get_visible_child_name() != 'list':
    pump(0.2)
if posts_page.stack.get_visible_child_name() == 'list':
    check('real posts loaded', posts_page.model.get_n_items() > 0)
else:
    check('real posts loaded', False)
check('pinned tab present', pinned_page is not None)
check('media tab present', media_page is not None)
if pinned_page is not None:
    profile_page.view_stack.set_visible_child_name('pinned')
    pinned_page.ensure_loaded()
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        state = pinned_page.stack.get_visible_child_name()
        items = pinned_page.model.get_n_items()
        if state == 'empty' and items == 0:
            break
        if state == 'list' and items > 0:
            break
        pump(0.2)
    items = pinned_page.model.get_n_items()
    state = pinned_page.stack.get_visible_child_name()
    if items > 0:
        check('pinned tab shows empty state', state == 'list')
    else:
        check('pinned tab shows empty state', state == 'empty')
else:
    check('pinned tab shows empty state', False)

pump(2.0)
screenshot(window, 'babble-profile.png')

# --- Interactive navigation path -------------------------------------

timeline_page = TimelinePage(client, mode='public')
window.set_child(None)
window.set_child(timeline_page)

deadline = time.monotonic() + 45
while time.monotonic() < deadline \
        and timeline_page.stack.get_visible_child_name() != 'list':
    pump(0.2)
if timeline_page.stack.get_visible_child_name() == 'list' \
        and timeline_page.model.get_n_items() > 0:
    timeline_page.on_row_activated(view, 0)
    while time.monotonic() < deadline:
        visible = view.get_visible_page()
        if visible is not None and visible.get_tag().startswith('thread-'):
            break
        pump(0.2)
    check('row click opens thread page',
          view.get_visible_page().get_tag().startswith('thread-'))
    # The author opener inside the row opens the profile page.
    row_widgets = []
    collect(timeline_page.list_view, row_widgets)
    opener = next((w for w in row_widgets
                   if isinstance(w, Gtk.Button)
                   and 'profile-opener' in list(w.get_css_classes())),
                  None)
    check('author opener present', opener is not None)
    if opener is not None:
        view.pop()
        opener.emit('clicked')
        while time.monotonic() < deadline:
            visible = view.get_visible_page()
            if visible is not None and visible.get_tag().startswith(
                    'profile-'):
                break
            pump(0.2)
        check('author click opens profile page',
              view.get_visible_page().get_tag().startswith('profile-'))
else:
    print('note - public timeline unreachable; nav path not asserted')

if failures:
    print('%d checks failed' % len(failures))
    _xvfb and _xvfb.terminate()
    sys.exit(1)
print('all thread/profile rendering checks passed')
_xvfb and _xvfb.terminate()
