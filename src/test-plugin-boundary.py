# SPDX-License-Identifier: GPL-3.0-or-later

"""The plugin boundary, as a test so it cannot drift silently.

`src/plugin.py` is the only module a plugin may import of Babble's: it
is the versioned contract, and every other module is free to change
between releases. Two rules keep that true, and a third keeps plugins
from leaking what they extend:

1. `plugin.py` imports only the standard library, so the contract does
   not quietly grow to mean "whatever it imports" as well.
2. A plugin shipped in `plugins/` imports nothing of Babble's but
   `babble.plugin` (its own package's modules are its own business).
3. A shipped plugin connects signals through its extension or Context
   (`self.connect` in an extension class; `context.connect` or
   `extension.connect`, however reached, such as `self.extension.connect`
   in a dialog), never with a bare `widget.connect`: Babble releases the first with what
   they belong to, while the second closes a cycle through C that keeps
   a whole page alive.

Third-party plugins are held to the same rules only by documentation;
these are the ones Babble ships, which are what others copy.

Sources are read with `ast`, as in `module boundary`; the checks are
functions so the self-checks below can feed them synthetic sources, and
the test does not pass vacuously before the first plugin exists.
"""

import ast
import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(srcdir)
plugins_dir = os.path.join(root, 'plugins')

STDLIB = sys.stdlib_module_names
# The last part of a receiver that is Babble's: its Context, or an
# extension (whose connect goes through the Context).
CONNECT_RECEIVERS = ('context', 'extension')

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def dotted(node):
    """``a.b.c`` for a Name/Attribute chain, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return '.'.join(reversed(parts))
    return None


def contract_violations(tree):
    """Imports of `plugin.py` outside the standard library."""
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names
                    if a.name.split('.')[0] not in STDLIB]
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or '').split('.')[0] \
                    not in STDLIB:
                bad += ['.' * node.level + (node.module or a.name)
                        for a in node.names]
    return bad


def import_violations(tree):
    """Imports of Babble's modules other than `babble.plugin`."""
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.')[0]
                if top in ('babble', 'src') and alias.name != 'babble.plugin':
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ''
            top = module.split('.')[0]
            if top == 'src':
                bad.append(module)
            elif module == 'babble':
                bad += ['babble.' + a.name for a in node.names
                        if a.name != 'plugin']
            elif top == 'babble' and module != 'babble.plugin':
                bad.append(module)
    return bad


def _is_extension_class(node):
    return any((dotted(base) or '').endswith('Extension')
               for base in node.bases)


def connect_violations(tree):
    """``x.connect(...)`` calls whose receiver Babble does not own: line
    numbers. ``self.connect`` counts as owned inside an extension class
    only; elsewhere ``self`` is a widget and that is a bare connect."""
    bad = []

    def visit(node, in_extension):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, _is_extension_class(child))
                continue
            if isinstance(child, ast.Call) \
                    and isinstance(child.func, ast.Attribute) \
                    and child.func.attr == 'connect':
                receiver = dotted(child.func.value)
                owned = (receiver or '').split('.')[-1] \
                    in CONNECT_RECEIVERS or (
                        receiver == 'self' and in_extension)
                if not owned:
                    bad.append(child.lineno)
            visit(child, in_extension)
    visit(tree, False)
    return bad


def parse(text):
    return ast.parse(text)


# Self-checks: each rule catches what it is for, and lets through what
# it should.
check('self-check: stdlib imports are fine in the contract',
      contract_violations(parse('import json\nfrom dataclasses import '
                                'dataclass\n')) == [])
check('self-check: the contract may not import gi or Babble',
      contract_violations(parse(
          'from gi.repository import Gtk\nfrom . import pages\n'))
      == ['gi.repository', '.pages'])
check('self-check: a plugin may import babble.plugin, three ways',
      import_violations(parse(
          'from babble import plugin\nimport babble.plugin\n'
          'from babble.plugin import Post\nfrom . import helpers\n'
          'from gi.repository import Gtk\n')) == [])
check('self-check: a plugin may not reach past the contract',
      import_violations(parse(
          'from babble import pages\nimport babble.mastodon\n'
          'from babble.statusrow import StatusRow\nfrom src import plugin\n'
          'from babble import plugin, window\n'))
      == ['babble.pages', 'babble.mastodon', 'babble.statusrow', 'src',
          'babble.window'])
check('self-check: connects through the extension or context pass',
      connect_violations(parse(
          'class P(plugin.HashtagPageExtension):\n'
          '    def f(self, b):\n'
          '        self.connect(b, "clicked", self.g)\n'
          '        self.context.connect(b, "clicked", self.g)\n'
          'def h(context, b):\n'
          '    context.connect(b, "clicked", print)\n'
          'class D(Adw.Dialog):\n'
          '    def f(self, b):\n'
          '        self.extension.connect(b, "clicked", self.g)\n')) == [])
check('self-check: a bare connect is caught, a widget\'s self too',
      connect_violations(parse(
          'class B(Gtk.Box):\n'
          '    def __init__(self):\n'
          '        self.connect("map", self.f)\n'
          'class P(plugin.PostMenuExtension):\n'
          '    def f(self, b):\n'
          '        b.connect("clicked", self.g)\n')) == [3, 6])

with open(os.path.join(srcdir, 'plugin.py'), encoding='utf-8') as source:
    contract = parse(source.read())
check('plugin.py imports only the standard library',
      contract_violations(contract) == [])

shipped = []
if os.path.isdir(plugins_dir):
    for folder, _dirs, files in os.walk(plugins_dir):
        shipped += [os.path.join(folder, f) for f in files
                    if f.endswith('.py')]
for path in sorted(shipped):
    name = os.path.relpath(path, root)
    with open(path, encoding='utf-8') as source:
        tree = parse(source.read())
    bad = import_violations(tree)
    check('%s imports only babble.plugin of Babble%s' % (
        name, ': ' + ', '.join(bad) if bad else ''), not bad)
    lines = connect_violations(tree)
    check('%s connects signals only through Babble%s' % (
        name, ' (lines %s)' % ', '.join(map(str, lines)) if lines else ''),
        not lines)

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
