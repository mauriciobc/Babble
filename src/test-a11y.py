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

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

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

# The suggestion lists: the text view keeps the focus, so it names the
# list it drives and the row that is current. PyGObject cannot read a
# relation's target back; these prove it is set while the list is shown
# and cleared when it goes (a CRITICAL from a malformed reference list
# is fatal in this file).
from gi.repository import GLib  # noqa: E402

from src.hashtagcompleter import HashtagCompleter  # noqa: E402
from src.mentioncompleter import MentionCompleter  # noqa: E402

ACTIVE = Gtk.AccessibleRelation.ACTIVE_DESCENDANT
CONTROLS = Gtk.AccessibleRelation.CONTROLS


class _Answer:
    path = ''


class _SuggestClient:
    instance = 'https://example.org'
    token = None

    def request_async(self, method, path, params=None, form=None,
                      json_body=None, callback=None, cancellable=None):
        result = _Answer()
        result.path = path
        GLib.idle_add(lambda *_: (callback(self, result),
                                  GLib.SOURCE_REMOVE)[1])

    def request_finish(self, result):
        if result.path == '/accounts/search':
            return [{'id': '1', 'acct': 'alice', 'username': 'alice',
                     'display_name': 'Alice'}]
        if result.path == '/api/v2/search':
            return {'hashtags': [{'name': 'linux'}, {'name': 'linuxmint'}]}
        return []


def pump(millis=100):
    deadline = GLib.get_monotonic_time() + millis * 1000
    context = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        context.iteration(False)


suggest_window = Gtk.Window()
suggest_view = Gtk.TextView()
suggest_window.set_child(suggest_view)
suggest_window.present()
suggest_view.grab_focus()
pump(200)
suggest_client = _SuggestClient()
for kind, completer, text, label in (
        ('hashtag', HashtagCompleter(suggest_client, suggest_view),
         '#lin', 'Suggested Hashtags'),
        ('mention', MentionCompleter(suggest_client, suggest_view),
         '@al', 'Suggested Accounts')):
    suggest_view.get_buffer().set_text(text)
    suggest_view.get_buffer().place_cursor(
        suggest_view.get_buffer().get_end_iter())
    pump(600)
    check('%s list is shown' % kind, completer.get_visible())
    check('%s list sets active-descendant while shown' % kind,
          has_relation(suggest_view, ACTIVE)
          and completer.list_box.get_selected_row() is not None)
    check('%s list sets controls while shown' % kind,
          has_relation(suggest_view, CONTROLS))
    check('%s list and its rows are named' % kind,
          has_label(completer.list_box)
          and has_label(completer.list_box.get_row_at_index(0))
          and completer.list_label == label)
    completer.list_box.unselect_all()
    check('%s active-descendant is cleared with the selection' % kind,
          not has_relation(suggest_view, ACTIVE)
          and has_relation(suggest_view, CONTROLS))
    completer.list_box.select_row(completer.list_box.get_row_at_index(0))
    check('%s active-descendant follows the selection' % kind,
          has_relation(suggest_view, ACTIVE))
    Escape = Gdk.KEY_Escape
    completer._on_key_pressed(None, Escape, 0, Gdk.ModifierType(0))
    check('%s relations are cleared by Escape' % kind,
          not has_relation(suggest_view, ACTIVE)
          and not has_relation(suggest_view, CONTROLS))
    suggest_view.get_buffer().set_text(text + 'x')
    suggest_view.get_buffer().place_cursor(
        suggest_view.get_buffer().get_end_iter())
    pump(600)
    completer.dismiss()
    check('%s relations are cleared on dismiss' % kind,
          not has_relation(suggest_view, ACTIVE)
          and not has_relation(suggest_view, CONTROLS))
    suggest_view.get_buffer().set_text('')
    pump(100)
suggest_window.destroy()

# Two completers share one text view: each clears only the relations it
# set, and typing prose makes no accessibility calls at all.
pair_window = Gtk.Window()
pair_view = Gtk.TextView()
pair_window.set_child(pair_view)
pair_window.present()
pair_view.grab_focus()
pump(200)
pair_mention = MentionCompleter(suggest_client, pair_view)
pair_hashtag = HashtagCompleter(suggest_client, pair_view)
pair_buffer = pair_view.get_buffer()


def pair_type(text):
    pair_buffer.set_text(text)
    pair_buffer.place_cursor(pair_buffer.get_end_iter())
    pump(600)


# The hashtag completer's handler runs after the mention one, and the
# other way round for the second pair of checks.
pair_type('@al')
check('the mention list is shown beside a hashtag completer',
      pair_mention.get_visible())
pair_hashtag.update()
check("the hashtag completer's update does not clear the mention list's "
      'relations',
      has_relation(pair_view, ACTIVE) and has_relation(pair_view, CONTROLS))
pair_type('#lin')
check('the hashtag list is shown beside a mention completer',
      pair_hashtag.get_visible())
pair_mention.update()
check("the mention completer's update does not clear the hashtag list's "
      'relations',
      has_relation(pair_view, ACTIVE) and has_relation(pair_view, CONTROLS))
pair_type('')
resets = []
real_reset = pair_view.reset_relation
pair_view.reset_relation = lambda relation: (resets.append(relation),
                                             real_reset(relation))[1]
for char in 'hello':
    pair_buffer.insert_at_cursor(char)
    pump(20)
pair_view.reset_relation = real_reset
check('prose typing makes no relation reset', not resets)
pair_window.destroy()

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all accessibility read-back checks passed')
