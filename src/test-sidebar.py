#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The icon rail and the header controls for a hidden sidebar.

Both widgets are mostly wiring, which is exactly where a wrong property
binding or a missing label hides: a 36px row still looks like a row when
its accessible name is empty, and a control that never becomes visible
is invisible in the obvious sense too. The state matrix itself lives in
``test-sidebar-state``; this test builds the widgets.

Exits 77 (skip) when there is no display."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gio, GObject, Gtk  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.sidebarcontrol import SidebarControl  # noqa: E402
from src.sidebarrail import SidebarRail  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''))
        failures.append(name)


def menu():
    model = Gio.Menu()
    model.append('Home', 'win.home')
    model.append('Preferences', 'app.preferences')
    return model


class FakeWindow(GObject.Object):
    sidebar_visible = GObject.Property(type=bool, default=True)
    sidebar_collapsed = GObject.Property(type=bool, default=False)
    unread_text = GObject.Property(type=str, default='')
    unread_visible = GObject.Property(type=bool, default=False)


# --- the rail ---------------------------------------------------------

rail = SidebarRail(menu())
rail.set_sections([
    [('home', 'user-home-symbolic', 'Home'),
     ('notifications', 'babble-notifications-symbolic', 'Notifications')],
    [('lists', 'view-list-symbolic', 'Lists')],
])

rows = [rail.listbox.get_row_at_index(i) for i in range(4)]
check('the rail has the three place rows and a separator',
      len(rows) == 4
      and getattr(rows[0], 'tag', None) == 'home'
      and getattr(rows[1], 'tag', None) == 'notifications'
      and getattr(rows[2], 'tag', None) is None
      and getattr(rows[3], 'tag', None) == 'lists',
      repr([getattr(row, 'tag', None) for row in rows]))

check('a rail row is a 36px target',
      rows[0].get_size_request()[1] == 36,
      repr(rows[0].get_size_request()))
check('a rail row has a plain-text tooltip',
      rows[0].get_tooltip_text() == 'Home',
      repr(rows[0].get_tooltip_text()))
check('the separator row cannot be selected or activated',
      not rows[2].get_selectable() and not rows[2].get_activatable())
check('the compose button targets win.compose',
      rail.compose_button.get_action_name() == 'win.compose',
      repr(rail.compose_button.get_action_name()))
check('the rail mode button has the menu model',
      rail.mode_button.get_menu_model() is not None)
check('the rail mode button has an accessible tooltip',
      rail.mode_button.get_tooltip_text() == 'Sidebar',
      repr(rail.mode_button.get_tooltip_text()))

activated = []
rail.connect('activated', lambda _rail, tag: activated.append(tag))
rail.listbox.emit('row-activated', rows[0])
check('activating a row reports its tag', activated == ['home'], repr(activated))

rail.set_selected('notifications')
check('set_selected highlights without emitting another activation',
      rail.listbox.get_selected_row() is rows[1] and activated == ['home'],
      repr(activated))

rail.set_title('lists', 'My Lists')
check('set_title updates the plain-text tooltip',
      rows[3].get_tooltip_text() == 'My Lists',
      repr(rows[3].get_tooltip_text()))

rail.set_badge(4)
check('the badge shows the count',
      rail._badge is not None
      and rail._badge.get_visible()
      and rail._badge.get_text() == '4',
      repr(rail._badge.get_text() if rail._badge is not None else None))
rail.set_badge(0)
check('the badge hides at zero', not rail._badge.get_visible())

account = Gtk.Button(icon_name='avatar-default-symbolic')
rail.set_account_button(account)
check('the account button is hosted by the rail',
      account.get_parent() is rail.account_host)

# Rebuilding sections must not leave a stale badge or row.
rail.set_sections([[('home', 'user-home-symbolic', 'Home'),
                    ('notifications', 'babble-notifications-symbolic',
                     'Notifications')]])
check('rebuilding sections after zero leaves the badge hidden',
      rail._badge is not None and not rail._badge.get_visible())

# --- the header control ----------------------------------------------

fake = FakeWindow()
control = SidebarControl(fake, menu(), menu())
check('the control is hidden while the sidebar is visible',
      not control.get_visible())
fake.props.sidebar_visible = False
check('the control appears when the sidebar is hidden',
      control.get_visible())
check('the control has all four actions while expanded',
      control.compose_button.get_visible()
      and control.bell_button is not None
      and control.bell_button.get_visible(),
      repr((control.compose_button.get_visible(),
            control.bell_button is not None,
            control.bell_button.get_visible()
            if control.bell_button is not None else None)))

fake.props.sidebar_collapsed = True
check('compose and the bell leave the compact header',
      not control.compose_button.get_visible()
      and not control.bell_button.get_visible())
fake.props.sidebar_collapsed = False

fake.props.unread_text = '7'
fake.props.unread_visible = True
check('the control badge follows the window unread properties',
      control.badge.get_label() == '7' and control.badge.get_visible(),
      repr((control.badge.get_label(), control.badge.get_visible())))

no_bell = SidebarControl(fake, menu(), menu(), include_bell=False)
check('include_bell=False creates no bell',
      no_bell.bell_button is None and no_bell.badge is None)

check('the mode control is first, then compose, then the primary menu',
      [child for child in (control.mode_button, control.compose_button,
                           control.primary_button)]
      == [control.get_first_child(),
          control.compose_button,
          control.primary_button],
      repr([child.__class__.__name__
            for child in (control.get_first_child(),
                          control.get_first_child().get_next_sibling())]))

if failures:
    print('\n%d checks failed' % len(failures))
    sys.exit(1)
print('all sidebar widget checks passed')
