pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'babble'


gnome.compile_resources('babble',
  'babble.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
)

python = import('python').find_installation('python3')

conf = configuration_data()
conf.set('PYTHON', python.full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'babble.in',
  output: 'babble',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)

install_data(
  '__init__.py',
  'accounts.py',
  'admin.py',
  'announcements.py',
  'compose.py',
  'conversations.py',
  'desktopnotify.py',
  'emojichooser.py',
  'explore.py',
  'fedicollections.py',
  'filters.py',
  'hashtags.py',
  'i18n.py',
  'lists.py',
  'main.py',
  'mastodon.py',
  'mediaviewer.py',
  'mentioncompleter.py',
  'notifications.py',
  'pages.py',
  'preferences.py',
  'profile.py',
  'profileedit.py',
  'relations.py',
  'report.py',
  'scheduled.py',
  'search.py',
  'searchprovider.py',
  'statustext.py',
  'statusviews.py',
  'streaming.py',
  'textemoji.py',
  'thread.py',
  'timeline.py',
  'window.py',
  'wrapped.py',
  install_dir: moduledir,
)

timeline_test_env = environment()
timeline_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
timeline_test_env.set('GSETTINGS_SCHEMA_DIR',
                      meson.project_build_root() / 'data')
timeline_test_env.set('GSETTINGS_BACKEND', 'memory')
test('timeline render', python,
  args: [files('test-timeline.py')],
  env: timeline_test_env,
  timeout: 120,
)

timeline_memory_test_env = environment()
timeline_memory_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
timeline_memory_test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
timeline_memory_test_env.set('GSETTINGS_BACKEND', 'memory')
timeline_memory_test_env.set('G_DEBUG', 'fatal-criticals')
test('timeline memory', python,
  args: [files('test-timeline-memory.py')],
  env: timeline_memory_test_env,
  timeout: 60,
)

thread_profile_test_env = environment()
thread_profile_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
thread_profile_test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
thread_profile_test_env.set('GSETTINGS_BACKEND', 'memory')
thread_profile_test_env.set('G_DEBUG', 'fatal-criticals')
test('thread profile render', python,
  args: [files('test-thread-profile.py')],
  env: thread_profile_test_env,
  timeout: 180,
)

compose_test_env = environment()
compose_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
compose_test_env.set('GSETTINGS_SCHEMA_DIR',
                     meson.project_build_root() / 'data')
compose_test_env.set('GSETTINGS_BACKEND', 'memory')
test('compose widgets', python,
  args: [files('test-compose.py')],
  env: compose_test_env,
  timeout: 120,
)

notifications_test_env = environment()
notifications_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
notifications_test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
notifications_test_env.set('GSETTINGS_BACKEND', 'memory')
notifications_test_env.set('G_DEBUG', 'fatal-criticals')
test('notifications render', python,
  args: [files('test-notifications.py')],
  env: notifications_test_env,
  timeout: 120,
)

oauth_test_env = environment()
oauth_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
oauth_test_env.set('BABBLE_BUILDDIR', meson.project_build_root())
oauth_test_env.set('G_DEBUG', 'fatal-criticals')
test('oauth callback', python,
  args: [files('test-oauth-callback.py')],
  env: oauth_test_env,
  timeout: 120,
)

loopback_test_env = environment()
loopback_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
loopback_test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
loopback_test_env.set('GSETTINGS_BACKEND', 'memory')
loopback_test_env.set('G_DEBUG', 'fatal-criticals')
test('oauth loopback', python,
  args: [files('test-oauth-loopback.py')],
  env: loopback_test_env,
  timeout: 120,
)

error_summary_test_env = environment()
error_summary_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
error_summary_test_env.set('G_DEBUG', 'fatal-criticals')
test('error summary', python,
  args: [files('test-error-summary.py')],
  env: error_summary_test_env,
  timeout: 120,
)

streaming_test_env = environment()
streaming_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
test('streaming helpers', python,
  args: [files('test-streaming.py')],
  env: streaming_test_env,
  timeout: 60,
)

account_store_test_env = environment()
account_store_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
test('account store', python,
  args: [files('test-account-store.py')],
  env: account_store_test_env,
  timeout: 60,
)

# Needs no display: the text layer is deliberately widget-free.
statustext_test_env = environment()
statustext_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
test('status text', python,
  args: [files('test-statustext.py')],
  env: statustext_test_env,
  timeout: 60,
)

searchprovider_test_env = environment()
searchprovider_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
searchprovider_test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
searchprovider_test_env.set('GSETTINGS_BACKEND', 'memory')
searchprovider_test_env.set('G_DEBUG', 'fatal-criticals')
test('search provider', python,
  args: [files('test-searchprovider.py')],
  env: searchprovider_test_env,
  timeout: 60,
)
