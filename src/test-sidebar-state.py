#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar state matrix and the structural rules it depends on.

The sidebar has two stored properties (style and hidden) plus a runtime
overlay flag when the window is narrow. Getting the derived values wrong
is invisible in a screenshot -- a 64 px rail can look like a wide
sidebar with the labels cut off -- so the matrix is asserted here, away
from GTK.

The source rules are the other half: the old shell was an
AdwNavigationSplitView and every caller returned to the content pane
with ``set_show_content(True)``. Those calls are an AttributeError once
the shell is an AdwOverlaySplitView, and one call site is easy to miss
because it lives outside window.py.
"""

import os
import sys
import xml.etree.ElementTree as ET

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src import sidebarmode  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' :: %s' % detail) if detail else ''))


# --- the matrix -------------------------------------------------------

check('full expanded: labels',
      sidebarmode.label_visible('full', False))
check('full collapsed: labels',
      sidebarmode.label_visible('full', True))
check('icons expanded: no labels',
      not sidebarmode.label_visible('icons', False))
check('icons collapsed: labels, because the overlay is a full pane',
      sidebarmode.label_visible('icons', True))

check('full expanded is not a rail',
      not sidebarmode.icon_rail('full', False))
check('icons expanded is a rail',
      sidebarmode.icon_rail('icons', False))
check('icons collapsed is not a rail',
      not sidebarmode.icon_rail('icons', True))
check('full widths are the sidebar defaults',
      sidebarmode.widths('full', False) == (180, 280))
check('icons expanded width is the rail width',
      sidebarmode.widths('icons', False) == (64, 64))
check('icons collapsed width returns to the overlay width',
      sidebarmode.widths('icons', True) == (180, 280))

check('expanded and not hidden is visible',
      sidebarmode.sidebar_visible(False, False, False))
check('expanded and hidden is not visible',
      not sidebarmode.sidebar_visible(True, False, False))
check('collapsed ignores the stored hidden flag until opened',
      not sidebarmode.sidebar_visible(True, True, False)
      and not sidebarmode.sidebar_visible(False, True, False))
check('collapsed and opened is visible',
      sidebarmode.sidebar_visible(True, True, True)
      and sidebarmode.sidebar_visible(False, True, True))

# --- the shell no longer has set_show_content -------------------------

for name in ('window.py', 'pages.py'):
    path = os.path.join(srcdir, name)
    with open(path, encoding='utf-8') as source:
        text = source.read()
    check('%s has no set_show_content' % name,
          'set_show_content' not in text)
    check('%s dismisses the overlay' % name,
          'dismiss_sidebar_overlay' in text)

# --- the template names the widgets the code expects ------------------

ui_path = os.path.join(srcdir, 'window.ui')
ui = ET.parse(ui_path).getroot()
by_id = {obj.get('id'): obj for obj in ui.iter('object') if obj.get('id')}


def prop(obj, name):
    for node in obj.findall('property'):
        if node.get('name') == name:
            return node
    return None


def iter_parents(node, parents):
    while node in parents:
        node = parents[node]
        yield node


split = by_id.get('split_view')
check('the shell is an AdwOverlaySplitView',
      split is not None and split.get('class') == 'AdwOverlaySplitView',
      split.get('class') if split is not None else 'missing')
if split is not None:
    check('the shell starts with the sidebar hidden',
          prop(split, 'show-sidebar') is not None
          and prop(split, 'show-sidebar').text == 'False')
    check('the shell pins its own visibility',
          prop(split, 'pin-sidebar') is not None
          and prop(split, 'pin-sidebar').text == 'True')

header = by_id.get('sidebar_header')
check('the sidebar header suppresses its title buttons',
      header is not None
      and prop(header, 'show-title') is not None
      and prop(header, 'show-title').text == 'False'
      and prop(header, 'show-start-title-buttons') is not None
      and prop(header, 'show-start-title-buttons').text == 'False'
      and prop(header, 'show-end-title-buttons') is not None
      and prop(header, 'show-end-title-buttons').text == 'False')

for object_id in ('sidebar_stack', 'sidebar_mode_button', 'sidebar_list',
                  'home_header', 'notifications_header',
                  'account_text_box', 'account_chevron'):
    check('%s exists in window.ui' % object_id, object_id in by_id)

# The mode button is one widget at the bottom of the sidebar's toolbar,
# outside the stack that swaps the full sidebar for the rail.
parents = {child: parent for parent in ui.iter() for child in parent}
mode_nodes = [obj for obj in ui.iter('object')
              if obj.get('id') == 'sidebar_mode_button']
check('window.ui has exactly one sidebar_mode_button', len(mode_nodes) == 1,
      repr(len(mode_nodes)))
if len(mode_nodes) == 1:
    slot = parents[mode_nodes[0]]
    toolbar = parents[slot]
    check('the mode button is a bottom bar child of the sidebar toolbar',
          slot.tag == 'child' and slot.get('type') == 'bottom'
          and toolbar.get('class') == 'AdwToolbarView'
          and header is not None
          and header in [o for c in toolbar.findall('child')
                         for o in c.findall('object')],
          repr((slot.get('type'), toolbar.get('class'))))
    check('the mode button is not in the sidebar header',
          header not in list(iter_parents(mode_nodes[0], parents)))

menu = ui.find("menu[@id='sidebar_mode_menu']")
targets = []
if menu is not None:
    for item in menu.iter('item'):
        for attribute in item.iter('attribute'):
            if attribute.get('name') == 'target':
                targets.append(attribute.text)
check('the mode menu offers full, icons and hidden',
      targets == ['full', 'icons', 'hidden'], repr(targets))

if failures:
    print('\n%d checks failed' % len(failures))
    sys.exit(1)
print('all sidebar state checks passed')
