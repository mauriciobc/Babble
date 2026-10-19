#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every dialog either owns a toast overlay or says why it does not.

A dialog covers the window's toast overlay, so a toast raised from inside
one lands behind it unless the dialog has an overlay of its own. That is
invisible in a screenshot and easy to forget when a dialog is added, so
it is checked statically here: no display, no bundle, no network.

A dialog is any class whose base chain reaches Adw.Dialog, Adw.Window
or any Adw.*Dialog (Adw.AlertDialog, Adw.PreferencesDialog, ...). The
set of dialogs found is compared with EXPECTED, so a dialog that stops
being detected (or a new one) fails until the list is updated.

A dialog owns an overlay when its class body, or the body of a class it
inherits from, contains a call to Adw.ToastOverlay (an AST check, so a
docstring or comment that mentions it does not count), or when it
derives from a libadwaita class in ADW_OWNED, which ships its own
overlay and add_toast (per Adw-1.gir only Adw.PreferencesDialog among
the dialogs; Adw.AlertDialog does not). Anything else must be on
ALLOWED with a reason; an entry that no longer matches, and an entry
with an empty reason, fail.

Every dialog other than pages.ToastDialog must either use that shared
base or be listed in HAND_BUILT, ALLOWED or derive from ADW_OWNED, so
an off-base dialog cannot be added silently.

The check is module-aware because more than one module defines a class
with the same name, and it resolves `from . import X as Y` aliases.
"""

import ast
import glob
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
srcdir = os.path.abspath(srcdir)

# Dialogs that legitimately have no overlay of their own. The reason is
# part of the check: an entry cannot exist without one, and deleting the
# reason fails rather than silently weakening the rule.
ALLOWED = {
    ('compose', 'ComposeDialog'):
        'the overlay is built by its child view BabbleComposeView '
        '(compose.py:454); CLEAN-03 records why it cannot use '
        'pages.show_toast',
    ('scheduled', 'SchedulePickerDialog'):
        'raises no toasts; errors are reported by the page behind it '
        'after the dialog closes',
}

# Dialogs that intentionally keep their own scaffold. They own an
# overlay (so they are not in ALLOWED), but they are not on the shared
# base because their content shape does not fit it. Each entry records
# why; the check fails if one of them starts using the base without the
# entry being removed.
HAND_BUILT = {
    ('admin', 'AdminDialog'):
        'a NavigationSplitView of admin pages, not a preferences page',
    ('profile', 'ProfileDialog'):
        'a bottom sheet whose content is a NavigationView',
    ('report', 'ReportDialog'):
        'a NavigationView with a header per step',
}

# libadwaita base classes that provide their own toast overlay and
# add_toast (checked against /usr/share/gir-1.0/Adw-1.gir).
ADW_OWNED = {
    'Adw.PreferencesDialog':
        'AdwPreferencesDialog has a built-in AdwToastOverlay and add_toast',
}

EXTERNAL = {
    'Adw.Dialog', 'Adw.Window', 'Gtk.Dialog', 'Gtk.Window',
    'Gtk.Widget', 'Gtk.Box', 'Adw.Bin', 'Gtk.Overlay',
}

# Every dialog class in src/, cross-checked once by importing the modules
# and walking Adw.Dialog subclasses at runtime. A dialog that is added,
# renamed or silently stops being detected fails until this is updated.
EXPECTED = {
    ('admin', 'AddFederationBlockDialog'),
    ('admin', 'AddIPBlockDialog'),
    ('admin', 'AdminAccountDialog'),
    ('admin', 'AdminDialog'),
    ('admin', 'ReportDialog'),
    ('admin', 'TakeActionDialog'),
    ('compose', 'ComposeDialog'),
    ('fedicollections', 'CollectionEditDialog'),
    ('hashtags', 'MultiHashtagEditor'),
    ('lists', 'ListEditor'),
    ('lists', '_MembershipDialog'),
    ('notifications', 'NotificationPolicyDialog'),
    ('pages', 'ToastDialog'),
    ('preferences', 'PreferencesDialog'),
    ('profile', 'PrivateNoteDialog'),
    ('profile', 'ProfileDialog'),
    ('profileedit', 'ProfileEditDialog'),
    ('report', 'ReportDialog'),
    ('scheduled', 'SchedulePickerDialog'),
    ('search', 'AdvancedSearchDialog'),
    ('wrapped', 'WrappedDialog'),
}

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        suffix = '' if detail is None or detail == '' else ': %s' % (detail,)
        print('FAIL - %s%s' % (name, suffix))


def module_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def constructs_overlay(node):
    return any(isinstance(sub, ast.Call)
               and ast.unparse(sub.func) == 'Adw.ToastOverlay'
               for sub in ast.walk(node))


classes = {}   # (module, name) -> {'bases': [...], 'overlay': bool}
imports = {}   # module -> {local name: (module, name)}
for path in sorted(glob.glob(os.path.join(srcdir, '*.py'))):
    module = module_name(path)
    if module.startswith('test-'):
        continue
    with open(path, encoding='utf-8') as source_file:
        source = source_file.read()
    tree = ast.parse(source)
    local = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module is None:
            # from . import pages as P: P is the module itself.
            for alias in node.names:
                local[alias.asname or alias.name] = (alias.name, None)
        if isinstance(node, ast.ImportFrom) and node.module:
            target = node.module.split('.')[-1]
            if node.level:
                target = node.module
            for alias in node.names:
                local[alias.asname or alias.name] = (target, alias.name)
        if isinstance(node, ast.ClassDef):
            classes[(module, node.name)] = {
                'bases': [ast.unparse(base) for base in node.bases],
                'overlay': constructs_overlay(node),
            }
    imports[module] = local


def resolve(module, base):
    """The (module, name) a base expression refers to, or None."""
    if base in EXTERNAL:
        return None
    if '.' in base:
        qualifier, name = base.rsplit('.', 1)
        if (qualifier, name) in classes:
            return (qualifier, name)
        alias = imports.get(module, {}).get(qualifier)
        if alias is not None:
            return (alias[0], name)
        return None
    target = imports.get(module, {}).get(base)
    if target is not None and target[1] is not None:
        return target
        return None
    if (module, base) in classes:
        return (module, base)
    return None


def is_dialog_base(base):
    return base == 'Adw.Window' or (base.startswith('Adw.')
                                    and base.endswith('Dialog'))


def adw_owned(key, seen=()):
    if key in seen or key not in classes:
        return False
    for base in classes[key]['bases']:
        if base in ADW_OWNED:
            return True
        target = resolve(key[0], base)
        if target and adw_owned(target, seen + (key,)):
            return True
    return False


def is_dialog(key, seen=()):
    if key in seen or key not in classes:
        return False
    for base in classes[key]['bases']:
        if is_dialog_base(base):
            return True
        target = resolve(key[0], base)
        if target and is_dialog(target, seen + (key,)):
            return True
    return False


def owns_overlay(key, seen=()):
    if key in seen or key not in classes:
        return False
    if classes[key]['overlay'] or adw_owned(key):
        return True
    for base in classes[key]['bases']:
        target = resolve(key[0], base)
        if target and owns_overlay(target, seen + (key,)):
            return True
    return False


dialogs = sorted({key for key in classes if is_dialog(key)})
found = set(dialogs)
check('exactly the expected %d dialog classes are found' % len(EXPECTED),
      found == EXPECTED,
      'missing: %s; unexpected: %s' % (
          ', '.join(sorted('%s.%s' % k for k in EXPECTED - found)) or '-',
          ', '.join(sorted('%s.%s' % k for k in found - EXPECTED)) or '-'))
print('    dialogs: %d' % len(dialogs))
for key in dialogs:
    print('    %s.%s' % key)
check('both ReportDialog classes are found, not merged',
      ('admin', 'ReportDialog') in classes
      and ('report', 'ReportDialog') in classes)

unowned = [key for key in dialogs if not owns_overlay(key)]
allowed_keys = set(ALLOWED)
reported = [key for key in unowned if key not in allowed_keys]
check('every dialog owns an overlay or is allowlisted', not reported,
      ', '.join('%s.%s' % key for key in reported))

def uses_base(key):
    if key not in classes:
        return False
    for base in classes[key]['bases']:
        target = resolve(key[0], base)
        if target and (target == ('pages', 'ToastDialog')
                       or uses_base(target)):
            return True
    return False


off_base = [key for key in dialogs
            if key != ('pages', 'ToastDialog') and not uses_base(key)
            and key not in HAND_BUILT and key not in ALLOWED
            and not adw_owned(key)]
check('every off-base dialog is listed', not off_base,
      ', '.join('%s.%s' % key for key in off_base))

for key, reason in sorted(ADW_OWNED.items()):
    check('%s has a reason' % key, bool(reason.strip()), reason)

for key, reason in sorted(HAND_BUILT.items()):
    check('%s.%s has a reason' % key, bool(reason.strip()), reason)
    check('%s.%s still exists and is a dialog' % key, key in dialogs, key)
    check('%s.%s is hand-built and owns its overlay' % key,
          key in dialogs and owns_overlay(key) and not uses_base(key),
          'it now uses the base; move it out of HAND_BUILT')

for key, reason in sorted(ALLOWED.items()):
    check('%s.%s has a reason' % key, bool(reason.strip()), reason)
    check('%s.%s still exists and is a dialog' % key,
          key in dialogs, key)
    check('%s.%s is not a stale entry (it owns no overlay)' % key,
          key in unowned, 'the class now has an overlay; drop the entry')

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all dialog toast-ownership checks passed')
