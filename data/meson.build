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

# DBusActivatable=true in the desktop file: the session bus starts the
# application as a service for Open, ActivateAction and the shell search.
service_conf = configuration_data()
service_conf.set('bindir', get_option('prefix') / get_option('bindir'))
configure_file(
  input: 'io.github.mauriciobc.Babble.service.in',
  output: 'io.github.mauriciobc.Babble.service',
  configuration: service_conf,
  install: true,
  install_dir: get_option('datadir') / 'dbus-1' / 'services',
)

install_data(
  'io.github.mauriciobc.Babble.search-provider.ini',
  install_dir: get_option('datadir') / 'gnome-shell' / 'search-providers',
)

# The Mallard help pages (F1, and the Help item in the primary menu).
# They are installed per language under share/help/<lang>/<app id>/,
# which is where a help:<app id> URI looks for them, so a translated
# page is a directory beside the English one and one more language below.
# They are not translated through po/: a Mallard page is its own
# document, not a string table, so a translator edits the page.
help_languages = ['C']
foreach help_lang : help_languages
  install_data(
    files(
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'index.page',
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'start.page',
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'reading.page',
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'writing.page',
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'desktop.page',
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'settings.page',
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'plugins.page',
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'problems.page',
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'keys.page',
      'help' / help_lang / 'io.github.mauriciobc.Babble' / 'legal.xml',
    ),
    install_dir: get_option('datadir') / 'help' / help_lang / 'io.github.mauriciobc.Babble',
  )
endforeach

install_data(
  'screenshots/timeline.png',
  'screenshots/catch-up.png',
  'screenshots/hashtag.png',
  'screenshots/composer.png',
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
