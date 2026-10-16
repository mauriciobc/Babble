project('babble', version: '0.2.0', meson_version: '>= 1.0.0')

dependency('gtk4', version: '>= 4.24')
dependency('libadwaita-1', version: '>= 1.10')
# Spell checking in the composer: libspelling, on a GtkSourceView buffer.
dependency('gtksourceview-5')
dependency('libspelling-1', version: '>= 0.4')

i18n = import('i18n')
gnome = import('gnome')

subdir('data')
subdir('src')
subdir('po')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
