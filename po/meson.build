# The bugtracker address is passed to xgettext rather than typed into
# po/babble.pot: that file's header is rewritten on every regeneration,
# so a value fixed there is gone the next time a string changes. This is
# the field a translator reads when a message is wrong, so it has to
# come from the build. (Meson's own msgid_bugs_address would say this
# more directly; it is not in the i18n module before 1.13.)
i18n.gettext('babble', preset: 'glib',
  args: ['--msgid-bugs-address=https://github.com/mauriciobc/Babble/issues'],
)

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