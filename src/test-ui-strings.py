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

The data files come from ``po/POTFILES.in`` rather than from a list here:
that file is what xgettext reads, so it is the one list that cannot drift
into disagreeing with what actually gets translated. The gschema carries
``gettext-domain="babble"``, so gsettings-editor resolves its ~57 key
summaries through the catalogue and a reader that stopped at ``src/`` would
have been blind to every one of them.

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

# The source tree is src/, so the files outside it -- po/, data/ -- are one
# level up, the way src/test-style-css.py finds them.
root = os.path.dirname(srcdir)

UI_FILES = ('window.ui', 'shortcuts-dialog.ui')

# The plugins Babble ships translate through its catalogue, so their
# strings are held to the same rules; named relative to the tree's root.
PLUGIN_FILES = sorted(
    os.path.relpath(os.path.join(folder, name), root)
    for folder, _dirs, names in os.walk(os.path.join(root, 'plugins'))
    for name in names if name.endswith('.py'))

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

# A translatable element in a data XML file: <summary> and <description> in
# the gschema, and the display name, the summary, a screenshot caption, the
# paragraphs and the list items in the AppStream metainfo. This is a
# different shape from a .ui value -- an element, not an attribute -- so it
# gets its own reader rather than a loosened UI_VALUE that would match
# markup. <default>, <nick>, <choice>, <id> and <control> are values, not
# prose, and are not in the list.
#
# Only the opening tag is matched here; the text up to the matching closing
# tag is taken separately, so that an element wrapping other elements (the
# metainfo puts its <p> and <li> inside <description>) is found and then
# skipped without swallowing the children the way a single pattern would.
DATA_OPEN = re.compile(
    r'<(summary|description|name|caption|p|li)\b([^>]*)>')

# The one opt-out both formats honour: an element can be marked so the
# translator never sees it.
NOT_TRANSLATABLE = re.compile(r'translatable\s*=\s*"no"')

# A translatable line in a .desktop entry: the keys gettext reads, in the
# main group and in an action group alike. Exec, MimeType and the action
# ids are not strings for the user.
DESKTOP_VALUE = re.compile(
    r'^(?:Name|GenericName|Comment|Keywords)=(.*)$', re.MULTILINE)


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


def data_strings_in(source):
    """(line, string) for every translatable element in data XML *source*.

    An element whose text still contains markup is a container -- the
    metainfo wraps its <p> and <li> in <description> -- and its prose is in
    the children, which are read on their own. Judging the container as
    well would check the same sentence twice, once with tags in it.
    """
    found = []
    for match in DATA_OPEN.finditer(source):
        end = source.find('</%s>' % match.group(1), match.end())
        if end < 0:
            continue
        text = source[match.end():end]
        if '<' in text or NOT_TRANSLATABLE.search(match.group(2)):
            continue
        line = source.count('\n', 0, match.start()) + 1
        found.append((line, ' '.join(text.split())))
    return found


def data_strings(path):
    with open(path, encoding='utf-8') as data:
        return data_strings_in(data.read())


def desktop_strings(path):
    """(line, string) for every translatable value in a .desktop entry."""
    with open(path, encoding='utf-8') as entry:
        source = entry.read()
    return [(source.count('\n', 0, match.start()) + 1, match.group(1).strip())
            for match in DESKTOP_VALUE.finditer(source)]


def potfile_entries():
    """The data/ paths po/POTFILES.in lists, in xgettext's order.

    That file is the one list that cannot drift: it is what xgettext reads,
    so a data file added there is a string the app translates and this test
    would otherwise never judge, and a file listed but missing is reported
    below rather than skipped silently. A second list here would be a
    second thing to forget.
    """
    listing = os.path.join(root, 'po', 'POTFILES.in')
    with open(listing, encoding='utf-8') as potfiles_file:
        return [line.strip() for line in potfiles_file
                if line.strip().startswith('data/')]


def potfiles():
    """(name, path, reader) for every data file po/POTFILES.in lists.

    Only the data/ entries are taken: the src/ ones are the AST and .ui
    passes, and reading them twice would double every count below.
    """
    found = []
    for entry in potfile_entries():
        path = os.path.join(root, entry)
        if not os.path.exists(path):
            print('    %s: listed in po/POTFILES.in but not in the tree'
                  % entry)
            continue
        if entry.endswith('.desktop.in'):
            found.append((entry, path, desktop_strings))
        elif entry.endswith(('.xml', '.xml.in')):
            found.append((entry, path, data_strings))
        else:
            print('    %s: listed in po/POTFILES.in with no reader here'
                  % entry)
    return found


DATA_FILES = potfiles()


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
    for name in PLUGIN_FILES:
        for line, text in python_strings(os.path.join(root, name)):
            found.append(('py', name, line, text))
    for name in UI_FILES:
        path = os.path.join(srcdir, name)
        if not os.path.exists(path):
            print('    %s: gone -- drop it from UI_FILES' % name)
            continue
        for line, text in ui_strings(path):
            found.append(('ui', name, line, text))
    for name, path, reader in DATA_FILES:
        for line, text in reader(path):
            found.append(('data', name, line, text))
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
for _name in PLUGIN_FILES:
    with open(os.path.join(root, _name), encoding='utf-8') as _file:
        for _number, _text in enumerate(_file.read().split('\n'), 1):
            SOURCES[(_name, _number)] = _text.strip()
for _name, _path, _reader in DATA_FILES:
    with open(_path, encoding='utf-8') as _file:
        for _number, _text in enumerate(_file.read().split('\n'), 1):
            SOURCES[(_name, _number)] = _text.strip()

# "OK", "Yes", "No" and "Done" are forbidden as ACTION labels -- a button,
# a dialog response, a menu item. They are not forbidden as values: an
# info row that renders a report property as "Forwarded: Yes" is data, and
# failing it would be this test insisting on a wording nobody asked for.
#
# A second kind stands down for literal notation: three periods that are
# part of a code sample, not a typographic ellipsis. The gschema documents
# a stored value as JSON, and "[...]" in a JSON array is how JSON writes an
# elided list -- reworded into an ellipsis character it would be a
# description of a value nobody can paste. Notation is excepted by naming
# it, so it cannot spread into prose by accident.
#
# Each entry is (source snippet, rule, reason). The rule is the one that
# stands down, so an entry that stops being a hit prints nothing and can
# be deleted. The snippet is how the exemption is matched, and it is
# checked against the file on every run: an entry whose snippet is gone
# prints a note and stops applying rather than sitting here as a permission
# slip for whatever replaces it.
EXCEPTIONS = {
    'admin.py': (
        ("info_row(_('Forwarded'), _('Yes'))", 'label',
         'a report property shown as a value in an info row, not a '
         'button or a dialog response'),
    ),
    'data/io.github.mauriciobc.Babble.gschema.xml': (
        ('"tags": [...]', 'three dots',
         'JSON notation for an elided list in a key summary: the brackets '
         'are what the stored value looks like, so the periods inside them '
         'are part of the sample and not a typographic ellipsis'),
    ),
}


def excepted(name, source, rule):
    """True when *source* is a recorded exception to *rule*."""
    for snippet, wanted, reason in EXCEPTIONS.get(name, ()):
        if wanted != rule or snippet not in source:
            continue
        print('    %s: %s -- excepted from the %s rule: %s'
              % (name, snippet, rule, reason))
        return True
    return False


def stale_exceptions(entries=None):
    """(name, snippet) for every exception whose snippet is gone.

    An exception for code that is gone is not a permission slip for the
    code that is, so it is called out here rather than waited for.
    """
    entries = EXCEPTIONS if entries is None else entries
    return [(name, snippet)
            for name, snippets in entries.items()
            for snippet, _rule, _reason in snippets
            if not any(snippet in line
                       for (file_name, _line), line in SOURCES.items()
                       if file_name == name)]


for _name, _snippet in stale_exceptions():
    print('    %s: %s is not in the file any more -- drop this entry'
          % (_name, _snippet))


def generic_label(_kind, name, _line, text, source):
    if label_without_mnemonic(text) not in GENERIC_LABELS:
        return False
    return not excepted(name, source, 'label')


def spelling(_kind, _name, _line, text, _source):
    return BRITISH.search(text) is not None


def latin(_kind, _name, _line, text, _source):
    return LATIN.search(text) is not None


def three_dots(_kind, name, _line, text, source):
    if THREE_DOTS.search(text) is None:
        return False
    return not excepted(name, source, 'three dots')


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

# The same near-misses, planted in the other reader: prose in an XML
# comment, a value that is not prose, an element the translator never
# sees, and a container whose prose lives in its children.
DATA_SAMPLE = '''\
<schemalist gettext-domain="babble">
  <!-- e.g. in a comment is a comment -->
  <key name="sample" type="s">
    <default>'e.g.'</default>
    <summary>A summary with e.g. in it</summary>
    <summary translatable="no">Kept out of the catalogue</summary>
    <description>Set the sample, e.g. for tests</description>
  </key>
  <description>
    <p>A paragraph...</p>
  </description>
</schemalist>
'''

DATA_SAMPLE_STRINGS = data_strings_in(DATA_SAMPLE)
check('a translatable element in a data file is read, prose and all',
      DATA_SAMPLE_STRINGS == [(5, 'A summary with e.g. in it'),
                              (7, 'Set the sample, e.g. for tests'),
                              (10, 'A paragraph...')])
check('an XML comment, a default and translatable="no" are not read',
      not any('comment' in text or "'e.g.'" in text
              or 'catalogue' in text for _l, text in DATA_SAMPLE_STRINGS))
check('a container element is not judged with its children inside it',
      all('<' not in text for _l, text in DATA_SAMPLE_STRINGS))
check('every data file po/POTFILES.in lists is read, and none is missing',
      len(DATA_FILES) == len(potfile_entries())
      and any('gschema' in name for name, _p, _r in DATA_FILES)
      and any('metainfo' in name for name, _p, _r in DATA_FILES))
check('a value use of a generic word is excepted, a label is not',
      excepted('admin.py', "info_row(_('Forwarded'), _('Yes'))", 'label')
      and not excepted('admin.py', "Gtk.Button(label=_('_Yes'))", 'label'))
check('an exception is scoped to the rule it names',
      excepted('admin.py', "info_row(_('Forwarded'), _('Yes'))", 'label')
      and not excepted('admin.py', "info_row(_('Forwarded'), _('Yes'))",
                       'three dots'))
check('an exception whose snippet is gone is reported, not applied',
      stale_exceptions({'admin.py': (('no such snippet', 'label', 'why'),)})
      and not stale_exceptions())
check('the label rule catches the label it is about',
      generic_label('py', 'x.py', 1, '_Done', "x = _('Done')"))
check('the label rule keeps a verb the app wants',
      not generic_label('py', 'x.py', 1, '_Delete List', ''))
check('the Latin rule is not tripped by ordinary text',
      not latin(None, None, None, 'Posts by one account', ''))
check('the three-dot rule is not tripped by an ellipsis character',
      not three_dots(None, None, None, 'Loading…', ''))
check('the three-dot rule stands down for JSON, and not for prose',
      not three_dots('data', 'data/io.github.mauriciobc.Babble.gschema.xml',
                     251, 'Saved multi-hashtag timelines as JSON '
                     '{"title", "tags": [...]}',
                     '<summary>Saved multi-hashtag timelines as JSON '
                     '{"title", "tags": [...]}</summary>')
      and three_dots('data', 'data/io.github.mauriciobc.Babble.gschema.xml',
                     251, 'Saved timelines...', '<summary>Saved...</summary>'))

STRINGS = collect()

rule('no generic action label (OK, Yes, No, Done)', STRINGS, generic_label)
rule('US spelling in every translatable string', STRINGS, spelling)
rule('no Latin abbreviation (e.g., i.e.)', STRINGS, latin)
rule('an ellipsis is U+2026, not three periods', STRINGS, three_dots)

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('all UI string checks passed')
