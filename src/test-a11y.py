#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""What accessibility information the UI actually exposes.

Reads attributes back through GTK's test accessibility backend, which is
the only way to ask a widget what an assistive technology would be given
without running Orca. The properties are what Orca presents; whether the
resulting speech is good is a question for a listening session, not for
this file.

Two halves. The checks assert what must hold today: a post row is an
article with a name naming the author and the booster, each dialog has
the dialog role, and the walk of each dialog really reaches its labels,
so an inventory of nothing cannot pass for a clean one. The inventories
print, as notes, the buttons in a post row with no name, no tooltip and
no visible text, and the visible labels in the preferences and report
dialogs that no relation marks as a control's label, because Orca's
guide says such a label may be read as static text. The inventories are
the input to the accessibility pass, not failures.

A relation in GTK lives on the control and points at its label, and
PyGObject cannot read the target back. So a label counts as related
when it sits in a row (which names its title and subtitle), or when the
nearest ancestor that is a control or carries LABELLED_BY carries it (a
button, or a preferences group or status page, which are labelled by
their title and described by their description). The dialogs are never
presented, so an Adw.Dialog has no children yet; the walk starts at the
dialog's content child.

Exits 77 (skip) without a display, and fails unless GTK_A11Y=test,
because no other backend can answer the read-back."""

import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

if os.environ.get('GTK_A11Y') != 'test':
    print('FAIL - GTK_A11Y must be "test" for this file, got %r'
          % os.environ.get('GTK_A11Y'))
    sys.exit(1)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + detail) if detail else ''))


def note(name, detail=''):
    print('    %s%s' % (name, (': ' + detail) if detail else ''))


def has_label(widget):
    """Whether *widget* carries an accessible name of its own."""
    return Gtk.test_accessible_has_property(
        widget, Gtk.AccessibleProperty.LABEL)


def has_relation(widget, relation):
    return Gtk.test_accessible_has_relation(widget, relation)


def has_role(widget, role):
    return Gtk.test_accessible_has_role(widget, role)


def walk(widget):
    """Every descendant of *widget*, each exactly once, depth first."""
    found, pending = [], [widget]
    while pending:
        node = pending.pop()
        found.append(node)
        children, child = [], node.get_first_child()
        while child is not None:
            children.append(child)
            child = child.get_next_sibling()
        pending.extend(reversed(children))
    return found


def has_text_descendant(widget):
    """Whether *widget* draws its own text (a GtkButton with a label)."""
    return any(isinstance(node, Gtk.Label) and node.get_visible()
               and node.get_label()
               for node in walk(widget))


def is_internal_toggle(widget):
    """A MenuButton's own toggle is an implementation detail, not UI."""
    return isinstance(widget.get_parent(), Gtk.MenuButton)


def within_row(widget):
    """Whether *widget* sits inside a row that names it."""
    node = widget.get_parent()
    while node is not None:
        if isinstance(node, (Gtk.ListBoxRow, Adw.ActionRow, Adw.EntryRow,
                             Adw.PreferencesRow, Adw.ExpanderRow)):
            return True
        node = node.get_parent()
    return False


# Widgets that take input and name themselves; a label under one of them
# belongs to it, so the search for a relation stops there.
CONTROLS = (Gtk.Button, Gtk.MenuButton, Gtk.CheckButton, Gtk.Switch,
            Gtk.DropDown, Gtk.Scale, Gtk.Editable)


def related_by_ancestor(label, root):
    """Whether the control or labelled container holding *label* is
    labelled by a relation.

    The relation sits on that ancestor and points at the label; its
    target cannot be read from Python, so the label is credited to the
    nearest ancestor (up to *root*) that is a control or carries one.
    """
    node = label.get_parent()
    while node is not None:
        if has_relation(node, Gtk.AccessibleRelation.LABELLED_BY):
            return True
        if isinstance(node, CONTROLS) or node is root:
            return False
        node = node.get_parent()
    return False


# -- the helpers themselves --------------------------------------------
plain = Gtk.Button()
labelled = Gtk.Button()
labelled.update_property([Gtk.AccessibleProperty.LABEL], ['Download'])
check('a button without a label reads as unlabelled', not has_label(plain))
check('a button with a label reads as labelled', has_label(labelled))
worded = Gtk.Button(label='Download')
hidden = Gtk.Button(label='Download')
hidden.get_child().set_visible(False)
check('a button with a visible label draws text',
      has_text_descendant(worded))
check('a button whose label is hidden draws no text',
      not has_text_descendant(hidden))
box = Gtk.Box()
inner = Gtk.Box()
leaf = Gtk.Label(label='x')
inner.append(leaf)
box.append(inner)
check('walk visits every descendant exactly once',
      walk(box) == [box, inner, leaf])


# -- a post row --------------------------------------------------------
from src import mediacache, statusrow, timelinedata  # noqa: E402

# No network and no textures: the row only has to be built and bound.
mediacache.AvatarCache.load = staticmethod(lambda avatar, account: None)
mediacache._TextureCache.load = staticmethod(
    lambda url, picture, cover=None: None)


def status(n, content, name='Ada Lovelace'):
    return {'id': str(n), 'content': content,
            'account': {'id': str(100 + n), 'display_name': name},
            'media_attachments': []}


class _Client:
    token = None
    instance_info = None


boosted = dict(status(2, '', name='Grace Hopper'), reblog=status(
    1, '<p>Hello</p>', name='Ada Lovelace'))
boosted['reblog']['media_attachments'] = [
    {'id': '1', 'type': 'image', 'url': 'https://example.org/a.png'}]
row = statusrow.StatusRow()
seen = []
original_update = row.update_property

def capture(props, values):
    if Gtk.AccessibleProperty.LABEL in props:
        seen.extend(values)
    return original_update(props, values)

row.update_property = capture
row.bind(timelinedata.StatusItem(boosted), _Client())

check('a post row has the article role',
      has_role(row, Gtk.AccessibleRole.ARTICLE))
check('a post row has an accessible name', has_label(row))
check('a boosted post names the author and the booster',
      seen == ['Post by Ada Lovelace, boosted by Grace Hopper'], seen)

for widget in walk(row):
    if not isinstance(widget, (Gtk.Button, Gtk.ToggleButton,
                               Gtk.MenuButton)):
        continue
    if has_label(widget) or widget.get_tooltip_text() \
            or has_text_descendant(widget) or is_internal_toggle(widget):
        continue
    note('interactive widget without a name',
         '%s.%s' % (type(widget).__name__,
                    ' '.join(widget.get_css_classes()) or '-'))


# -- dialogs: labels that may be read as static text -------------------
def report_unrelated_labels(dialog, name):
    content = dialog.get_child()
    labels = [widget for widget in walk(content)
              if isinstance(widget, Gtk.Label)] if content else []
    check('%s: the walk reaches the labels in the dialog' % name,
          len(labels) > 0, '%d labels walked' % len(labels))
    silent, in_row, related, unrelated = 0, 0, 0, []
    for label in labels:
        if not (label.get_visible() and label.get_label()):
            silent += 1
        elif within_row(label):
            in_row += 1
        elif related_by_ancestor(label, content):
            related += 1
        else:
            unrelated.append(label.get_label())
    note('%s: %d labels walked' % (name, len(labels)),
         '%d hidden or empty, %d in a row, %d related, %d unrelated'
         % (silent, in_row, related, len(unrelated)))
    note('%s: labels with no relation and no row' % name,
         '%d, first: %r' % (len(unrelated), unrelated[:5]))
    return unrelated


builddir = os.environ.get('BABBLE_BUILDDIR') or os.getcwd()
bundle = os.path.join(builddir, 'src', 'babble.gresource')
if not os.path.exists(bundle):
    print('SKIP - no compiled babble.gresource; run meson compile first')
    sys.exit(77)
Gio.Resource.load(bundle)._register()

from src import main as babble_main  # noqa: E402
from src import preferences, report  # noqa: E402
from src.window import BabbleWindow  # noqa: E402

Gio.Application.register(babble_main.BabbleApplication(version='test'), None)
app = Gio.Application.get_default()
window = BabbleWindow(application=app)

prefs = preferences.PreferencesDialog(window)
check('the preferences dialog has the dialog role',
      has_role(prefs, Gtk.AccessibleRole.DIALOG))
report_unrelated_labels(prefs, 'preferences')


class _ReportClient:
    instance_info = {}
    token = None

    def instance_info_async(self, callback, *args, **kwargs):
        return None

    def request_async(self, *_args, **_kwargs):
        return None


account = {'id': '1', 'acct': 'ada@example.org',
           'display_name': 'Ada Lovelace'}
try:
    report_dialog = report.ReportDialog(_ReportClient(), account)
except Exception as error:  # noqa: BLE001 - reported, not swallowed
    print('FAIL - the report dialog could not be built: %r' % error)
    failures.append('report dialog')
else:
    check('the report dialog has the dialog role',
          has_role(report_dialog, Gtk.AccessibleRole.DIALOG))
    report_unrelated_labels(report_dialog, 'report')

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all accessibility read-back checks passed')
