i18n.gettext('babble', preset: 'glib')

# A msgstr whose format specifiers do not match its msgid raises at
# runtime, so check every catalogue in LINGUAS.
msgfmt = find_program('msgfmt', required: false, disabler: true)
foreach lang : import('fs').read('LINGUAS').strip().split('\n')
  test('po check ' + lang, msgfmt,
    args: ['--check', '--check-format', '-o', '/dev/null',
           files(lang + '.po')])
endforeach
