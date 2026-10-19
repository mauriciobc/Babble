#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every dialog either owns a toast overlay or says why it does not.

A dialog covers the window's toast overlay, so a toast raised from inside
one lands behind it unless the dialog has an overlay of its own. That is
invisible in a screenshot and easy to forget when a dialog is added, so
it is checked statically here: no display, no bundle, no network.

A class passes when its own source, or the source of a class it inherits
from, constructs an Adw.ToastOverlay. Anything else must be on ALLOWED
with a reason; an entry that no longer matches, and an entry with an
empty reason, fail. The check is module-aware because more than one
module defines a class with the same name.
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

EXTERNAL = {
    'Adw.Dialog', 'Adw.Window', 'Gtk.Dialog', 'Gtk.Window',
    'Gtk.Widget', 'Gtk.Box', 'Adw.Bin', 'Gtk.Overlay',
}

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (': ' + str(detail)) if detail else ''))


def module_name(path):
    return os.path.splitext(os.path.basename(path))[0]


classes = {}   # (module, name) -> {'bases': [...], 'source': str}
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
        if isinstance(node, ast.ImportFrom) and node.module:
            target = node.module.split('.')[-1]
            if node.level:
                target = node.module
            for alias in node.names:
                local[alias.asname or alias.name] = (target, alias.name)
        if isinstance(node, ast.ClassDef):
            classes[(module, node.name)] = {
                'bases': [ast.unparse(base) for base in node.bases],
                'source': ast.get_source_segment(source, node) or '',
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
    if base in imports.get(module, {}):
        return imports[module][base]
    if (module, base) in classes:
        return (module, base)
    return None


def is_dialog(key, seen=()):
    if key in seen or key not in classes:
        return False
    for base in classes[key]['bases']:
        if base in ('Adw.Dialog', 'Adw.Window') or base.endswith('.Dialog'):
            return True
        target = resolve(key[0], base)
        if target and is_dialog(target, seen + (key,)):
            return True
    return False


def owns_overlay(key, seen=()):
    if key in seen or key not in classes:
        return False
    if 'Adw.ToastOverlay' in classes[key]['source']:
        return True
    for base in classes[key]['bases']:
        target = resolve(key[0], base)
        if target and owns_overlay(target, seen + (key,)):
            return True
    return False


dialogs = sorted({key for key in classes if is_dialog(key)})
check('every dialog class is found', len(dialogs) >= 20, len(dialogs))
print('    dialogs: %d' % len(dialogs))
check('both ReportDialog classes are found, not merged',
      ('admin', 'ReportDialog') in classes
      and ('report', 'ReportDialog') in classes)

unowned = [key for key in dialogs if not owns_overlay(key)]
allowed_keys = set(ALLOWED)
reported = [key for key in unowned if key not in allowed_keys]
check('every dialog owns an overlay or is allowlisted', not reported,
      ', '.join('%s.%s' % key for key in reported))

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
