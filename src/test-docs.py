# SPDX-License-Identifier: GPL-3.0-or-later

"""The documentation rules, as a test so they cannot drift silently.

The documentation is the one part of the project nothing compiles and
nothing exercises. A label is renamed, a shortcut moves, a help page is
forgotten in one language: the suite stays green, the build ships, and
the documentation quietly stops being true of the software. That is the
failure this test exists to make visible.

It holds four things, all of them mechanical:

* **The help pages stay in step across languages.** The English set and
  every translated set carry the same files, the same page and section
  ids, and cross-references that resolve inside their own set. A
  translation that lags behind becomes a fork, and the next translator
  has no source to work from.

* **A language is registered where it is used.** Every directory under
  data/help/ is in ``help_languages`` in data/meson.build, and every
  registered language has a directory and every file the install list
  names. A translator who adds a directory and forgets the registration
  ships a help set that is installed nowhere.

* **The manual's cross-references resolve.** Every link and every anchor
  in Docs/manual/ points at something that exists. The manual is long
  and heavily cross-referenced, and a link that breaks reads as an
  assertion that something is missing.

* **The manual's label table is the catalogue's.** The table between
  ``<!-- label-table:start -->`` and ``<!-- label-table:end -->`` in
  chapter 10 is generated from po/pt_BR.po, never edited by hand, and
  the test fails when it differs from what the catalogue produces.
  Regenerate it with

      python3 src/test-docs.py --write-table

Every rule is proved on a synthetic input too, the way the project's
other rule-tests do: a check that cannot fail is not a check.

This test needs no display.
"""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import re
import sys
import xml.etree.ElementTree as ET

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(os.path.abspath(srcdir))

APP_ID = 'io.github.mauriciobc.Babble'
ML = '{http://projectmallard.org/1.0/}'
XL = '{http://www.w3.org/1999/xlink}'
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'

failures = 0


def check(label, condition):
    global failures
    if not condition:
        failures += 1
        print('FAIL - %s' % label)
    else:
        print('ok - %s' % label)


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


# --- the help pages ----------------------------------------------------

help_dir = os.path.join(root, 'data', 'help')
languages = sorted(name for name in os.listdir(help_dir)
                   if os.path.isdir(os.path.join(help_dir, name)))
check('data/help holds at least one language (%d)' % len(languages),
      bool(languages))

meson = read(os.path.join(root, 'data', 'meson.build'))
registered = re.search(r"help_languages\s*=\s*\[([^\]]*)\]", meson)
registered_languages = re.findall(r"'([^']+)'", registered.group(1)) \
    if registered else []
check('data/meson.build declares help_languages', registered is not None)
check('every language directory is registered (%d unregistered)'
      % len(set(languages) - set(registered_languages)),
      not set(languages) - set(registered_languages))
check('every registered language has a directory (%d missing)'
      % len(set(registered_languages) - set(languages)),
      not set(registered_languages) - set(languages))

# The files the install list names, shared by every language. The list
# holds path expressions ('help' / help_lang / APP / 'index.page'), so
# the file name is the last quoted string on each line.
install = re.search(r"install_data\(\s*files\((.*?)\)\s*,\s*install_dir",
                    meson, re.S)
install_files = []
if install:
    for line in install.group(1).splitlines():
        quoted = re.findall(r"'([^']+)'", line)
        if quoted:
            install_files.append(quoted[-1])
check('data/meson.build names the help files to install', bool(install_files))
missing = []
for language in languages:
    for name in install_files:
        path = os.path.join(help_dir, language, APP_ID, name)
        if not os.path.exists(path):
            missing.append('%s/%s' % (language, name))
for name in missing:
    print('    %s is installed for a language that does not have it' % name)
check('every installed help file exists in every language (%d missing)'
      % len(missing), not missing)


def page_ids(tree):
    """The ids of a Mallard document: its pages, sections and licenses."""
    found = set()
    for element in tree.iter():
        if element.tag in (ML + 'page', ML + 'section', ML + 'license'):
            identifier = element.get('id')
            if identifier:
                found.add(identifier)
    return found


def cross_references(tree):
    """Every reference a Mallard document makes."""
    found = set()
    for element in tree.iter():
        value = element.get('xref')
        if value:
            found.add(value)
        value = element.get(XL + 'href')
        if value:
            found.add(value)
    return found


def load(language):
    """The parsed documents of a language, by file name."""
    directory = os.path.join(help_dir, language, APP_ID)
    documents = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(('.page', '.xml')):
            documents[name] = ET.parse(os.path.join(directory, name)).getroot()
    return documents


sets = {language: load(language) for language in languages}

reference = sets[languages[0]]
reference_files = set(reference)
reference_ids = set()
for tree in reference.values():
    reference_ids |= page_ids(tree)

for language in languages[1:]:
    documents = sets[language]
    check('%s carries the same files as %s' % (language, languages[0]),
          set(documents) == reference_files)
    ids = set()
    for tree in documents.values():
        ids |= page_ids(tree)
    check('%s carries the same ids as %s' % (language, languages[0]),
          ids == reference_ids)
    for name in sorted(reference_files & set(documents)):
        tree = documents[name]
        for reference_value in cross_references(tree):
            if reference_value.startswith('http'):
                continue
            target, _, anchor = reference_value.partition('#')
            if target and target not in {n.split('.')[0] for n in documents}:
                check('%s/%s resolves %s' % (language, name, reference_value),
                      False)
            elif anchor and anchor not in ids:
                check('%s/%s resolves %s' % (language, name, reference_value),
                      False)

for language in languages:
    for name, tree in sorted(sets[language].items()):
        info = tree.find(ML + 'info')
        is_license = tree.tag == ML + 'license'
        titles = [t.get('type') for t in info.findall(ML + 'title')] \
            if info is not None else []
        check('%s/%s has an info block with a title' % (language, name),
              info is not None and 'text' in titles and 'link' in titles)
        if not is_license:
            check('%s/%s has a description' % (language, name),
                  info is not None and info.find(ML + 'desc') is not None)
            check('%s/%s declares its language' % (language, name),
                  tree.get(XML_LANG) == language)
        seen = set()
        duplicated = []
        for element in tree.iter():
            identifier = element.get('id')
            if identifier:
                if identifier in seen:
                    duplicated.append(identifier)
                seen.add(identifier)
        check('%s/%s has no duplicate id (%s)'
              % (language, name, ', '.join(duplicated) or 'none'),
              not duplicated)

# --- the manual --------------------------------------------------------

manual_dir = os.path.join(root, 'Docs', 'manual')
chapters = sorted(name for name in os.listdir(manual_dir)
                   if name.endswith('.md'))
check('the manual has a chapter per file (%d)' % len(chapters),
      len(chapters) >= 2)

for name in chapters:
    text = read(os.path.join(manual_dir, name))
    if name == 'README.md':
        for field in ('Identificador / versão', 'Data de emissão',
                      'Escopo', 'Propósito', 'Histórico'):
            check('the manual front matter names %s' % field, field in text)
        # The field names travel with the item: a manual written in
        # Portuguese calls the approval field "Aprovação". What is fixed
        # is the field, not its label.
        check('the manual front matter names the approval',
              'Aprovado por' in text or 'Aprovação' in text)
        continue
    check('%s carries the manual identifier' % name, 'BABBLE-MAN-001' in text)
    check('%s carries a version and a status' % name,
          re.search(r'v\d+\.\d+\s+—\s+\w', text) is not None)

# Every link and anchor in the manual resolves.


def anchors_of(path):
    found = set()
    for line in read(path).splitlines():
        match = re.match(r'^(#{1,6})\s+(.*)$', line)
        if match:
            title = match.group(2).strip().lower()
            title = re.sub(r'`([^`]*)`', r'\1', title)
            title = re.sub(r'[^\w\s\-]', '', title, flags=re.U)
            found.add(title.strip().replace(' ', '-'))
    return found


broken = []
for name in chapters:
    path = os.path.join(manual_dir, name)
    text = read(path)
    for target in re.findall(r'\]\(([^)]+)\)', text):
        if target.startswith(('http://', 'https://', 'mailto:')):
            continue
        filename, _, anchor = target.partition('#')
        other = os.path.join(manual_dir, filename) if filename else path
        if filename and not os.path.exists(other):
            broken.append('%s -> %s' % (name, target))
            continue
        if anchor and anchor not in anchors_of(other):
            broken.append('%s -> %s' % (name, target))
for item in broken:
    print('    %s does not resolve' % item)
check('every link in the manual resolves (%d broken)' % len(broken),
      not broken)

# --- the manual's label table, generated from the catalogue ------------

TABLE_START = '<!-- label-table:start -->'
TABLE_END = '<!-- label-table:end -->'
CHAPTER_TEN = os.path.join(manual_dir, '10-referencia.md')


def catalogue(path):
    """The msgid -> msgstr map of a gettext catalogue."""
    entries = {}
    identifier = value = None
    state = None
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.rstrip('\n')
            if line.startswith('msgid '):
                if identifier is not None:
                    entries[identifier] = value
                identifier = re.match(r'msgid "(.*)"$', line).group(1)
                value = None
                state = 'id'
            elif line.startswith('msgstr '):
                value = re.match(r'msgstr "(.*)"$', line).group(1)
                state = 'str'
            elif line.startswith('"') and state == 'id':
                identifier += re.match(r'"(.*)"$', line).group(1)
            elif line.startswith('"') and state == 'str':
                value += re.match(r'"(.*)"$', line).group(1)
    if identifier is not None:
        entries[identifier] = value
    return entries


def is_a_label(candidate):
    """Whether a backticked span in the manual is a piece of interface text.

    A label is short, has a handful of words, and is not a sentence:
    the sentences are quoted prose, and the long ones are paths,
    commands and messages that the catalogue would answer in English.
    """
    if not candidate or len(candidate) > 60:
        return False
    if candidate.endswith(('.', '?', '!', ':')):
        return False
    if len(candidate.split()) > 5:
        return False
    if '/' in candidate or candidate.startswith(('~', '#')):
        return False
    return True


def cited_labels():
    """The interface text the manual cites, outside its own label table."""
    found = set()
    for name in chapters:
        text = read(os.path.join(manual_dir, name))
        if name == '10-referencia.md':
            start = text.find(TABLE_START)
            end = text.find(TABLE_END)
            if start >= 0 and end > start:
                text = text[:start] + text[end:]
        for match in re.finditer(r'`([^`\n]{2,60})`', text):
            candidate = match.group(1)
            if is_a_label(candidate):
                found.add(candidate)
    return found


def render_table(entries):
    """The markdown table of the labels the manual cites and the
    catalogue translates."""
    lines = ['| Rótulo em inglês | Em português (pt-BR) |', '|---|---|']
    for label in sorted(cited_labels()):
        translated = entries.get(label)
        if translated:
            lines.append('| `%s` | %s |' % (label, translated))
    return '\n'.join(lines)


def splice_table(text, table):
    """*text* with the generated block replaced by *table*, or None when
    the markers are missing."""
    start, end = text.find(TABLE_START), text.find(TABLE_END)
    if start < 0 or end < start:
        return None
    return (text[:start + len(TABLE_START)] + '\n' + table + '\n'
            + text[end:])


entries = catalogue(os.path.join(root, 'po', 'pt_BR.po'))
check('the pt_BR catalogue has entries (%d)' % len(entries), bool(entries))

chapter_ten = read(CHAPTER_TEN)
fresh = splice_table(chapter_ten, render_table(entries))
if '--write-table' in sys.argv and fresh is not None:
    with open(CHAPTER_TEN, 'w', encoding='utf-8') as handle:
        handle.write(fresh)
    print('wrote %s' % CHAPTER_TEN)
    chapter_ten = fresh
check('splice_table needs both markers',
      splice_table('no markers', 'x') is None
      and splice_table('%s\nold\n%s' % (TABLE_START, TABLE_END), 'new')
      == '%s\nnew\n%s' % (TABLE_START, TABLE_END))
check('the label table is not empty',
      len(render_table(entries).splitlines()) > 2)
check('the manual label table is current '
      '(regenerate: python3 src/test-docs.py --write-table)',
      fresh is not None and fresh == chapter_ten)

# --- the policy --------------------------------------------------------

policy = read(os.path.join(root, 'Docs', 'documentation-policy.md'))
for field in ('BABBLE-POL-001', 'Approved by', 'Scope', 'Purpose',
              'Change history'):
    check('the documentation policy names %s' % field, field in policy)

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('all documentation checks passed')
