i18n.gettext('babble', preset: 'glib')

# A msgstr whose format specifiers do not match its msgid raises at
# runtime, so check every catalogue in LINGUAS.
#
# The check is opt-out, not opt-in: `disabler: true` on a missing
# msgfmt made a run that checked no translations at all look green.
# Say so at configure time instead, and let CI -- which installs gettext
# -- be the place that guarantees the catalogues are checked.
msgfmt = find_program('msgfmt', required: false, disabler: true)
if not msgfmt.found()
  warning('msgfmt not found: the "po check" tests will not run')
endif
foreach lang : import('fs').read('LINGUAS').strip().split('\n')
  test('po check ' + lang, msgfmt,
    args: ['--check', '--check-format', '-o', '/dev/null',
           files(lang + '.po')])
endforeach