desktop_file = i18n.merge_file(
  input: 'io.github.mauriciobc.Babble.desktop.in',
  output: 'io.github.mauriciobc.Babble.desktop',
  type: 'desktop',
  po_dir: '../po',
  install: true,
  install_dir: get_option('datadir') / 'applications',
)

metainfo_file = i18n.merge_file(
  input: 'io.github.mauriciobc.Babble.metainfo.xml.in',
  output: 'io.github.mauriciobc.Babble.metainfo.xml',
  po_dir: '../po',
  install: true,
  install_dir: get_option('datadir') / 'metainfo',
)

install_data(
  'io.github.mauriciobc.Babble.gschema.xml',
  install_dir: get_option('datadir') / 'glib-2.0' / 'schemas',
)

install_data(
  'screenshots/timeline.png',
  'screenshots/thread.png',
  install_dir: get_option('datadir') / 'metainfo' / 'io.github.mauriciobc.Babble' / 'screenshots',
)

gnome.compile_schemas(build_by_default: true, depend_files: 'io.github.mauriciobc.Babble.gschema.xml')

# Each validator is optional so a machine missing one still builds, but
# a missing validator must not read as a passing run: warn at configure
# time, and let CI -- which installs them -- be the guarantee.
desktop_validate = find_program('desktop-file-validate', required: false, disabler: true)
if not desktop_validate.found()
  warning('desktop-file-validate not found: the desktop file will not be validated')
endif
test('validate desktop file', desktop_validate, args: [desktop_file])

appstreamcli = find_program('appstreamcli', required: false, disabler: true)
if not appstreamcli.found()
  warning('appstreamcli not found: the metainfo will not be validated')
endif
test('validate metainfo', appstreamcli,
  args: ['validate', '--no-net', '--strict', metainfo_file])

compile_schemas = find_program('glib-compile-schemas', required: false, disabler: true)
if not compile_schemas.found()
  warning('glib-compile-schemas not found: the schema will not be validated')
endif
test('validate gschema', compile_schemas,
  args: ['--strict', '--dry-run', meson.current_source_dir()])

subdir('icons')
