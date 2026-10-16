# SPDX-License-Identifier: GPL-3.0-or-later

"""The UI writing style, as a test so it cannot drift back silently.

Four rules from the GNOME HIG writing style that the app has no other
way to keep, because every one of them is invisible in review once the
file is in place: no "OK"/"Yes"/"No"/"Done" as an action label, US
spelling, no Latin abbreviations, and an ellipsis as U+2026 rather than
three periods. None of them is a crash or a wrong value, which is exactly
why they come back: a translatable string is data, so a reviewer reads
the code around it and never the word in it.

The strings are collected the way the toolkit collects them, so nothing
that is not actually translated is judged. In Python that is a literal
passed to ``_()`` or ``ngettext()`` -- comments and docstrings are not
AST string arguments to a call, so they cannot be flagged here, which is
deliberate: the source is full of "e.g." in prose and in ``# Translators:``
notes, and flagging those would train the reader to ignore the output. In
``.ui`` it is the text of a ``translatable="yes"`` property or attribute.

The rules are in rule-checking form (a spelling is a spelling), so this
prints every offending string with its file and line rather than stopping
at the first one.
"""

import ast
import os
import re
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))

UI_FILES = ('window.ui', 'shortcuts-dialog.ui')

# The HIG forbids these as action labels: they say nothing about what the
# button does. A mnemonic underscore on either end is GTK's marker, not
# part of the label.
GENERIC_LABELS = ('OK', 'Yes', 'No', 'Done')

BRITISH = re.compile(
    r'\b(?:favourites?|favourited|colours?|coloured|behaviours?|centre'
    r'|organisations?|cancelled|licence|grey|recognises?|recognised'
    r'|labelled|whilst|amongst|analyse|analysed|initialise|initialised'
    r'|customise|customised)\b',
    re.IGNORECASE)

LATIN = re.compile(r'\b(?:e\.g\.|i\.e\.)')

# Three periods, not U+2026. \.{3} also catches four and more, which is
# the same mistake repeated.
THREE_DOTS = re.compile(r'\.{3}')

# A translatable value in a .ui file: the attribute carrying
# translatable="yes", then its text up to the closing tag. DOTALL because
# a value may be wrapped across lines.
UI_VALUE = re.compile(r'translatable="yes"[^>]*>(.*?)</', re.DOTALL)


def strings_in(source, filename):
    """(line, string) for every literal marked for translation in
    *source*.

    Only literals that are an argument to the gettext helpers. Anything
    built from a name, a concatenation the parser folds, or a format
    string with no literal to read is not returned: there is nothing to
    check, and guessing at it would be how a false positive gets in.
    """
    tree = ast.parse(source, filename=filename)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id == '_':
            wanted = 1
        elif node.func.id == 'ngettext':
            wanted = 2
        else:
            continue
        for arg in node.args[:wanted]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.append((arg.lineno, arg.value))
    return found


def python_strings(path):
    with open(path, encoding='utf-8') as module:
        return strings_in(module.read(), path)


def ui_strings(path):
    """(line, string) for every translatable="yes" value in a .ui file."""
    with open(path, encoding='utf-8') as ui:
        source = ui.read()
    found = []
    for match in UI_VALUE.finditer(source):
        line = source.count('\n', 0, match.start()) + 1
        found.append((line, ' '.join(match.group(1).split())))
    return found


def label_without_mnemonic(text):
    """The label as the reader sees it: no surrounding whitespace, and no
    leading or trailing underscore, which is GTK's mnemonic marker."""
    return text.strip().strip('_').strip()


def collect():
    """(kind, file, line, string) for everything translated in the tree."""
    found = []
    names = sorted(name for name in os.listdir(srcdir)
                   if name.endswith('.py') and name != '__init__.py')
    for name in names:
        for line, text in python_strings(os.path.join(srcdir, name)):
            found.append(('py', name, line, text))
    for name in UI_FILES:
        path = os.path.join(srcdir, name)
        if not os.path.exists(path):
            print('    %s: gone -- drop it from UI_FILES' % name)
            continue
        for line, text in ui_strings(path):
            found.append(('ui', name, line, text))
    return found


# The source line each string came from, so a rule can read the call it is
# an argument to rather than guessing from the string alone.
SOURCES = {}
for _name in sorted(os.listdir(srcdir)):
    if not _name.endswith(('.py', '.ui')):
        continue
    with open(os.path.join(srcdir, _name), encoding='utf-8') as _file:
        for _number, _text in enumerate(_file.read().split('\n'), 1):
            SOURCES[(_name, _number)] = _text.strip()

# "OK", "Yes", "No" and "Done" are forbidden as ACTION labels -- a button,
# a dialog response, a menu item. They are not forbidden as values: an
# info row that renders a report property as "Forwarded: Yes" is data, and
# failing it would be this test insisting on a wording nobody asked for.
#
# Each entry is (source snippet, reason). The snippet is how the exemption
# is matched, and it is checked against the file on every run: an entry
# whose snippet is gone prints a note and stops applying rather than
# sitting here as a permission slip for whatever replaces it.
VALUE_EXCEPTIONS = {
    'admin.py': (
        ("info_row(_('Forwarded'), _('Yes'))",
         'a report property shown as a value in an info row, not a '
         'button or a dialog response'),
    ),
}


def excepted_as_value(name, source):
    """True when *source* is a recorded value use of a generic word."""
    for snippet, reason in VALUE_EXCEPTIONS.get(name, ()):
        if snippet in source:
            print('    %s: %s -- excepted as a value: %s'
                  % (name, snippet, reason))
            return True
    return False


# An exception for code that is no longer there is not a permission slip
# for the code that is, so a stale one is called out here rather than
# waited for.
for _name, _entries in VALUE_EXCEPTIONS.items():
    for _snippet, _reason in _entries:
        if not any(_snippet in _line
                   for (_f, _l), _line in SOURCES.items() if _f == _name):
            print('    %s: %s is not in the file any more -- drop this '
                  'entry' % (_name, _snippet))


def generic_label(_kind, name, _line, text, source):
    if label_without_mnemonic(text) not in GENERIC_LABELS:
        return False
    return not excepted_as_value(name, source)


def spelling(_kind, _name, _line, text, _source):
    return BRITISH.search(text) is not None


def latin(_kind, _name, _line, text, _source):
    return LATIN.search(text) is not None


def three_dots(_kind, _name, _line, text, _source):
    return THREE_DOTS.search(text) is not None


failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL - %s' % label)
    else:
        print('ok - %s' % label)


def rule(label, found, offender):
    """Report every string *offender* rejects, as one check.

    *offender* gets the whole finding (kind, file, line, string and the
    source line it came from), so a rule can look at where a string is
    used rather than only at what it says.
    """
    hits = []
    for kind, name, line, text in found:
        if offender(kind, name, line, text, SOURCES.get((name, line), '')):
            hits.append('    %s:%d %s: %r' % (name, line, kind, text))
    for hit in hits:
        print(hit)
    check('%s (%d strings checked, %d offenders)'
          % (label, len(found), len(hits)), not hits)


# --- The reader must see the strings, or nothing below proves anything ----
#
# A rule test that stopped finding strings would pass on a tree full of
# violations, so these check the extraction itself against a source that
# plants every kind of near-miss: prose in a docstring, a marked call in
# a comment, and a concatenation the parser folds.

SAMPLE = '''\
"""Docstring with e.g. in it, and Done as prose."""


def sample(rows, plural):
    # _('Done') in a comment is a comment, not a label.
    return ngettext('%s follower', '%s followers', plural) % (
        _('%s pinned to the sidebar'), rows)
'''

SAMPLE_STRINGS = strings_in(SAMPLE, 'sample.py')
check('a docstring is prose, not a translatable string',
      all(text not in ('Done', 'e.g.') for _l, text in SAMPLE_STRINGS))
check('a marked call in a comment is not read',
      all(text != 'Done' for _l, text in SAMPLE_STRINGS))
check('both ngettext forms and a folded literal are seen',
      SAMPLE_STRINGS == [(6, '%s follower'), (6, '%s followers'),
                         (7, '%s pinned to the sidebar')])
check('a translatable value in .ui is read, whole and unwrapped',
      all(ui_strings(os.path.join(srcdir, name))
          and all(text and '<' not in text
                  for _l, text in ui_strings(os.path.join(srcdir, name)))
          for name in UI_FILES
          if os.path.exists(os.path.join(srcdir, name))))
check('a value use of a generic word is excepted, a label is not',
      excepted_as_value('admin.py', "info_row(_('Forwarded'), _('Yes'))")
      and not excepted_as_value('admin.py', "Gtk.Button(label=_('_Yes'))"))
check('the label rule catches the label it is about',
      generic_label('py', 'x.py', 1, '_Done', "x = _('Done')"))
check('the label rule keeps a verb the app wants',
      not generic_label('py', 'x.py', 1, '_Delete List', ''))
check('the Latin rule is not tripped by ordinary text',
      not latin(None, None, None, 'Posts by one account', ''))
check('the three-dot rule is not tripped by an ellipsis character',
      not three_dots(None, None, None, 'Loading…', ''))

STRINGS = collect()

rule('no generic action label (OK, Yes, No, Done)', STRINGS, generic_label)
rule('US spelling in every translatable string', STRINGS, spelling)
rule('no Latin abbreviation (e.g., i.e.)', STRINGS, latin)
rule('an ellipsis is U+2026, not three periods', STRINGS, three_dots)

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('all UI string checks passed')
