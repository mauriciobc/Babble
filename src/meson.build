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
  'auth.py',
  'catchup.py',
  'catchupdigest.py',
  'catchupgrid.py',
  'catchuprank.py',
  'catchupstore.py',
  'charts.py',
  'capabilities.py',
  'compose.py',
  'composeupload.py',
  'conversations.py',
  'desktopnotify.py',
  'drafts.py',
  'clipboard.py',
  'emojichooser.py',
  'entities.py',
  'explore.py',
  'fedicollections.py',
  'filterrules.py',
  'filters.py',
  'imagecache.py',
  'hashtags.py',
  'i18n.py',
  'lists.py',
  'main.py',
  'mastodon.py',
  'mediaviewer.py',
  'mentioncompleter.py',
  'notificationcopy.py',
  'notificationctrl.py',
  'notifications.py',
  'pages.py',
  'pollbox.py',
  'portals.py',
  'preferences.py',
  'profile.py',
  'profileactions.py',
  'profileedit.py',
  'relations.py',
  'report.py',
  'scheduled.py',
  'search.py',
  'searchprovider.py',
  'sidebarcontrol.py',
  'sidebarmode.py',
  'sidebarrail.py',
  'sourceline.py',
  'statustext.py',
  'statusviews.py',
  'streaming.py',
  'sysstate.py',
  'textemoji.py',
  'thread.py',
  'timelinebase.py',
  'timelinedata.py',
  'timelinepage.py',
  'statusrun.py',
  'statusactions.py',
  'statusmenu.py',
  'statusrow.py',
  'mediacache.py',
  'mediabox.py',
  'webcolumn.py',
  'window.py',
  'wrapped.py',
  install_dir: moduledir,
)

# One environment for every test. GTK_A11Y=none is what stops a missing
# AT-SPI registry from aborting the tests that run under
# G_DEBUG=fatal-criticals; NO_AT_BRIDGE alone does not. no-portals keeps
# the first GTK import from starting xdg-desktop-portal and stalling the
# later D-Bus tests. fatal_test_env is the same plus fatal criticals.
#
# LC_ALL/LANG are pinned to C because the widget suites assert geometry,
# and a translated string is a different length. `compose widgets` and
# `timeline memory` fail under a pt_BR desktop and pass under C, purely
# because the labels change the widths and heights they compare against.
# GitHub runners already default to C.UTF-8, so CI never saw this; it only
# ever appeared for whoever's desktop was not English, which is what made
# it look like Xvfb contention. Pinning here means a local run matches CI.
#
# A test that genuinely wants a translation should set LANGUAGE itself
# and restart -- see the interface-language path in babble.in -- rather
# than rely on the ambient locale.
#
# GSK_RENDERER=cairo because none of these tests test rendering, and
# letting GSK pick a GL renderer makes the suite depend on a working GL
# context that a CI container has no reason to have. When it picks one and
# cannot get a context, GDK logs "gdk_gl_context_make_current() failed" and
# libepoxy then aborts the process on
#   Assertion `0 && "Couldn't find current GLX or EGL context"' failed
# which took `mediaviewer teardown` down with SIGABRT -- under Xvfb and
# under headless mutter alike, so this was never a compositor problem.
# GSK_DEBUG=fallback does not save it; the abort happens inside the GL
# call. Cairo is what bench/ already pins for the same reason.
#
# The DISPLAY guards in the tests accept WAYLAND_DISPLAY as well, so the
# suite can be run under a Wayland compositor without every widget test
# reporting a skip. Under Xvfb (xvfb-run's default screen; ci.yml runs the
# mutter line below): xvfb-run -a dbus-run-session -- meson test -C build -j1.
# `thread profile render` and `oauth callback` spawn their own Xvfb and force
# GDK_BACKEND=x11, so the Xvfb binary must be installed either way.
# Under Wayland, GNOME 40+ mutter headless:
#   XDG_RUNTIME_DIR=$(mktemp -d) dbus-run-session -- \
#     mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- \
#     meson test -C build -j1
# The monitor size has to match the Xvfb screen: at 1024x768 the
# geometry-sensitive tests (compose widgets, timeline memory) fail,
# because the dialogs they measure get a smaller window. Both give
# 49 passed / 1 skipped (streaming helpers, which needs a secret service).
test_env = environment()
test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
test_env.set('BABBLE_BUILDDIR', meson.project_build_root())
test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
test_env.set('GSETTINGS_BACKEND', 'memory')
test_env.set('GDK_DEBUG', 'no-portals')
test_env.set('NO_AT_BRIDGE', '1')
test_env.set('GTK_A11Y', 'none')
test_env.set('LANG', 'C')
test_env.set('LC_ALL', 'C')
test_env.set('GSK_RENDERER', 'cairo')

fatal_test_env = environment()
fatal_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
fatal_test_env.set('BABBLE_BUILDDIR', meson.project_build_root())
fatal_test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
fatal_test_env.set('GSETTINGS_BACKEND', 'memory')
fatal_test_env.set('GDK_DEBUG', 'no-portals')
fatal_test_env.set('NO_AT_BRIDGE', '1')
fatal_test_env.set('GTK_A11Y', 'none')
fatal_test_env.set('LANG', 'C')
fatal_test_env.set('LC_ALL', 'C')
fatal_test_env.set('GSK_RENDERER', 'cairo')
fatal_test_env.set('G_DEBUG', 'fatal-criticals')

test('timeline render', python,
  args: [files('test-timeline.py')],
  env: test_env,
  timeout: 120,
)

# 'timeline memory' and 'compose widgets' wait on GTK frames and the
# garbage collector. Meson 1.12 has no per-test retries; the waits are
# conditions (TEST-09).
test('timeline memory', python,
  args: [files('test-timeline-memory.py')],
  env: fatal_test_env,
  timeout: 60,
)
test('thread profile render', python,
  args: [files('test-thread-profile.py')],
  env: fatal_test_env,
  timeout: 180,
)
test('compose widgets', python,
  args: [files('test-compose.py')],
  env: test_env,
  timeout: 120,
)
test('notifications render', python,
  args: [files('test-notifications.py')],
  env: fatal_test_env,
  timeout: 120,
)
test('oauth callback', python,
  args: [files('test-oauth-callback.py')],
  env: fatal_test_env,
  timeout: 120,
)
test('oauth loopback', python,
  args: [files('test-oauth-loopback.py')],
  env: fatal_test_env,
  timeout: 120,
)
test('error summary', python,
  args: [files('test-error-summary.py')],
  env: fatal_test_env,
  timeout: 120,
)
test('admin permissions', python,
  args: [files('test-admin.py')],
  env: test_env,
  timeout: 60,
)
test('entities', python,
  args: [files('test-entities.py')],
  env: test_env,
  timeout: 60,
)
test('profile relationship helpers', python,
  args: [files('test-profile-relationship.py')],
  env: test_env,
  timeout: 60,
)
test('mastodon rest retry', python,
  args: [files('test-mastodon-retry.py')],
  env: test_env,
  timeout: 60,
)
test('client transport', python,
  args: [files('test-client-transport.py')],
  env: test_env,
  timeout: 120,
)
# Boost card text is drawn by Gtk.Inscription, measured without laying
# the text out.
test('boost card', python,
  args: [files('test-boost-card.py')],
  env: fatal_test_env,
  timeout: 60,
)
# Off-screen runs get their cards built and measured before they are shown.
test('card prefetch', python,
  args: [files('test-prefetch.py')],
  env: fatal_test_env,
  timeout: 60,
)
# Drives a real local HTTP server, because the behaviour it pins is what
# the fetcher does when a request is cancelled mid-decode: the key must
# not be stranded, and one caller's cancellation must not take the image
# away from another caller sharing the request.
test('image cache', python,
  args: [files('test-imagecache.py')],
  env: fatal_test_env,
  timeout: 120,
)
test('media limits', python,
  args: [files('test-media-limits.py')],
  env: test_env,
  timeout: 60,
)
test('mediaviewer helpers', python,
  args: [files('test-mediaviewer.py')],
  env: test_env,
  timeout: 60,
)
# The media teardown test plays a video and tears it down again and
# again: the crash it guards against is a race between the widget's
# disposal and a thread inside GstPlay, so it needs the loop, and the
# loop needs time. fatal criticals, because a GTK critical in the
# teardown is as much a failure as the segfault is.
test('mediaviewer teardown', python,
  args: [files('test-mediaviewer-teardown.py')],
  env: fatal_test_env,
  timeout: 300,
)
# WebKit's sandbox (bubblewrap) cannot start inside a build sandbox or CI
# container (GitHub's Ubuntu runners restrict the unprivileged user
# namespaces it needs), so this test turns it off for its own web process
# only.
web_test_env = environment()
web_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
web_test_env.set('BABBLE_BUILDDIR', meson.project_build_root())
web_test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
web_test_env.set('GSETTINGS_BACKEND', 'memory')
web_test_env.set('GDK_DEBUG', 'no-portals')
web_test_env.set('NO_AT_BRIDGE', '1')
web_test_env.set('GTK_A11Y', 'none')
web_test_env.set('WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS', '1')
web_test_env.set('G_DEBUG', 'fatal-criticals')
test('web column', python,
  args: [files('test-webcolumn.py')],
  env: web_test_env,
  timeout: 120,
)
test('mediabox keyboard', python,
  args: [files('test-mediabox.py')],
  env: fatal_test_env,
  timeout: 60,
)
test('notifications error policy', python,
  args: [files('test-notifications-errors.py')],
  env: test_env,
  timeout: 60,
)
test('search helpers', python,
  args: [files('test-search.py')],
  env: fatal_test_env,
  timeout: 120,
)
test('streaming helpers', python,
  args: [files('test-streaming.py')],
  env: test_env,
  timeout: 60,
)
test('account store', python,
  args: [files('test-account-store.py')],
  env: test_env,
  timeout: 60,
)
test('local drafts', python,
  args: [files('test-drafts-local.py')],
  env: test_env,
  timeout: 60,
)
test('preferences schema', python,
  args: [files('test-preferences-schema.py')],
  env: test_env,
  timeout: 60,
)
# Needs no display: the text layer is deliberately widget-free.
test('status text', python,
  args: [files('test-statustext.py')],
  env: test_env,
  timeout: 60,
)
# Needs no display: the Catch Up data layer imports no gi at all.
test('catchup digest', python,
  args: [files('test-catchup-digest.py')],
  env: test_env,
  timeout: 60,
)
test('file size rule', python,
  args: [files('test-file-size.py')],
  env: test_env,
  timeout: 60,
)
test('capability gates', python,
  args: [files('test-capability.py')],
  env: test_env,
  timeout: 60,
)
test('version gate call sites', python,
  args: [files('test-version-gates.py')],
  env: test_env,
  timeout: 60,
)
test('filter rules', python,
  args: [files('test-filter-rules.py')],
  env: test_env,
  timeout: 60,
)
test('stream events', python,
  args: [files('test-stream-events.py')],
  env: test_env,
  timeout: 60,
)
test('catchup rank', python,
  args: [files('test-catchup-rank.py')],
  env: test_env,
  timeout: 60,
)
test('search provider', python,
  args: [files('test-searchprovider.py')],
  env: fatal_test_env,
  timeout: 180,
)
test('catchup page', python,
  args: [files('test-catchup.py')],
  env: fatal_test_env,
  timeout: 120,
)
test('collections', python,
  args: [files('test-collections.py')],
  env: test_env,
  timeout: 60,
)
test('catchup grid', python,
  args: [files('test-catchup-grid.py')],
  env: fatal_test_env,
  timeout: 120,
)

# Rules and shell-level behaviour that have no widget to look at: the
# action set (app.shortcuts is libadwaita's, F1 is Help), the stylesheet
# (a CSS parse error is otherwise invisible headlessly), the UI strings
# (a button labelled "Done" is otherwise a review finding), and the
# schedule picker's spin rows.
test('app lifecycle', python,
  args: [files('test-lifecycle.py')],
  env: fatal_test_env,
  timeout: 120,
)
test('schedule dialog', python,
  args: [files('test-scheduled-dialog.py')],
  env: fatal_test_env,
  timeout: 60,
)
# The sidebar's derived state and the shell-shape rules need no display;
# the rail and header-control widgets do, and run with fatal criticals.
test('sidebar state', python,
  args: [files('test-sidebar-state.py')],
  env: test_env,
  timeout: 60,
)
test('sidebar widgets', python,
  args: [files('test-sidebar.py')],
  env: fatal_test_env,
  timeout: 60,
)
test('ui strings', python,
  args: [files('test-ui-strings.py')],
  env: test_env,
  timeout: 60,
)
# The test collects GtkCssProvider's parsing-error signal, which is what
# catches a malformed rule. Fatal warnings are the second net, for the
# warnings GTK raises outside that signal.
css_env = environment()
css_env.set('BABBLE_SRCDIR', meson.current_source_dir())
css_env.set('BABBLE_BUILDDIR', meson.project_build_root())
css_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
css_env.set('GSETTINGS_BACKEND', 'memory')
css_env.set('GDK_DEBUG', 'no-portals')
css_env.set('NO_AT_BRIDGE', '1')
css_env.set('GTK_A11Y', 'none')
css_env.set('G_DEBUG', 'fatal-warnings')
test('style css', python,
  args: [files('test-style-css.py')],
  env: css_env,
  timeout: 60,
)

# The accessibility test backend answers "what would an assistive
# technology be given" without running one. Every other environment
# disables accessibility (GTK_A11Y=none), so this is the only test that
# reads attributes back.
a11y_test_env = environment()
a11y_test_env.set('BABBLE_SRCDIR', meson.current_source_dir())
a11y_test_env.set('BABBLE_BUILDDIR', meson.project_build_root())
a11y_test_env.set('GSETTINGS_SCHEMA_DIR', meson.project_build_root() / 'data')
a11y_test_env.set('GSETTINGS_BACKEND', 'memory')
a11y_test_env.set('GDK_DEBUG', 'no-portals')
a11y_test_env.set('NO_AT_BRIDGE', '1')
a11y_test_env.set('GTK_A11Y', 'test')
a11y_test_env.set('LANG', 'C')
a11y_test_env.set('LC_ALL', 'C')
a11y_test_env.set('GSK_RENDERER', 'cairo')
a11y_test_env.set('G_DEBUG', 'fatal-criticals')
test('a11y readback', python,
  args: [files('test-a11y.py')],
  env: a11y_test_env,
  timeout: 60,
)

# Static: every dialog owns a toast overlay or is allowlisted with a
# reason. No display, bundle or network is needed.
test('dialog toasts', python,
  args: [files('test-dialog-toasts.py')],
  env: test_env,
  timeout: 30,
)

# The behaviour both dialog bases promise, pinned before they are merged
# so the merge can be checked against it.
test('dialog bases', python,
  args: [files('test-dialog-bases.py')],
  env: fatal_test_env,
  timeout: 60,
)

# Behaviour of the migrated dialogs, written against what each exposed
# before the migration so it runs unchanged on both sides of it.
test('dialog behaviour', python,
  args: [files('test-dialog-behaviour.py')],
  env: fatal_test_env,
  timeout: 60,
)

# The sidebar and web-column breakpoints, at every pixel across both
# edges. Needs a 1280x1024 screen to reach the wide layout and skips
# (desktop-first) when the screen is smaller.
test('breakpoints', python,
  args: [files('test-breakpoints.py')],
  env: fatal_test_env,
  timeout: 120,
)

# The opt-in translucent sidebar, rendered and read back on both sides of
# the web column's 1100sp edge. Needs 1280x1024; skips when smaller.
test('translucent sidebar', python,
  args: [files('test-translucent-sidebar.py')],
  env: fatal_test_env,
  timeout: 60,
)

# The Wrapped Style menu, rendered: each style must change the card that
# Save as Image exports, and Accent must keep the chart bars visible.
test('wrapped styles', python,
  args: [files('test-wrapped-styles.py')],
  env: fatal_test_env,
  timeout: 60,
)
