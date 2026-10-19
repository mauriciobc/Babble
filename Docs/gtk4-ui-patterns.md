# GTK 4 / libadwaita UI patterns: a cross-project benchmark

How eight GTK 4 applications chosen for language, format and scale coverage structure their user
interfaces: which widgets they reach for, how they separate UI definitions
from behaviour, how they adapt to narrow windows, and where their code
diverges from current libadwaita guidance. Snapshot date: 2026-10-19.

## Scope and method

### Why these eight

This is a selection for coverage and relevance, **not a popularity ranking**.
The apps were chosen because together they span the axes that change UI code
in practice, and because two of them (Tuba, Fractal) are social and messaging
clients close to Babble:

- **Language:** C, Rust, Vala, Python (PyGObject) and GJS.
- **UI format:** Blueprint (`.blp`) and hand-written GtkBuilder XML (`.ui`).
- **Scale:** 8 to 148 UI files, from a single-window player to a file manager
  with a 4k-line custom sidebar.
- **Provenance:** GNOME core apps (Nautilus, Text Editor), GNOME Circle/World
  apps (Fractal, Amberol, Shortwave) and independent projects (Tuba, Bottles,
  Workbench).

Flathub install counts (Flathub stats API, 2026-10-19) show how uneven the
popularity is. Nautilus ships with GNOME, so its Flathub figure is not
meaningful.

| App | Flathub installs, total | Last month |
|---|---|---|
| Bottles | 5,419,866 | 136,663 |
| Shortwave | 1,065,905 | 19,261 |
| Text Editor | 576,473 | 11,204 |
| Amberol | 391,567 | 6,027 |
| Fractal | 102,327 | 1,519 |
| Workbench | 100,913 | 1,420 |
| Tuba | 70,979 | 1,319 |
| Nautilus (default app) | 10,928 | 266 |

Three larger GTK 4 apps were **not** scanned: Loupe (482,685), Papers
(227,318) and Builder (209,920). Each outranks Tuba, Fractal and Workbench on
installs. Counts and conclusions below describe these eight, not "the most
popular apps"; adding Loupe and Papers is the obvious way to widen the sample.

| App | Language | UI format | GTK floor | libadwaita floor | Commit | UI files | App CSS |
|---|---|---|---|---|---|---|---|
| Nautilus (Files) | C11 | Blueprint | 4.24.0 | 1.8.alpha | `8fd4b29` | 33 `.blp` + 3 `.ui` menus | 391 |
| Text Editor | C (gnu17) | GtkBuilder XML | 4.19.4 | 1.8.rc | `16542f0` | 18 `.ui` | 168 |
| Fractal | Rust 2024 | Blueprint | 4.20.2 | 1.8.0 | `95622c4` | 148 `.blp` + 1 `.ui` | 1,202 SCSS |
| Amberol | Rust 2018 | Blueprint | 4.19.5 | 1.8.rc | `003cbf2` | 8 `.blp` | 158 |
| Shortwave | Rust 2024 | GtkBuilder XML | 4.16 | 1.8.alpha | `f417e1a` | 22 `.ui` | 141 |
| Tuba | Vala | GtkBuilder XML | 4.19 | 1.8 | `02305ed` | 40 `.ui` | 1,347 |
| Bottles | Python 3 | Blueprint | none declared | none declared | `cfd9665` | 78 `.blp` | 259 |
| Workbench | GJS | Blueprint | 4.10 (C helper only) | 1.5 (C helper only) | `f5832a0` | 10 app `.blp` + 116 demos | 66 |

Floors are from `meson.build` or `Cargo.toml`. Bottles declares no API floor;
its Flatpak manifest targets `org.gnome.Platform` 50. Workbench's app itself
declares nothing; the numbers are the floors of its C helper library, while the
Flatpak runs the GNOME 51 SDK. UI-file counts exclude help, tests and bundled
demos; "App CSS" counts the application stylesheet(s), not demo or easter-egg
CSS.

Repositories, all on their main branch: Nautilus
(`gitlab.gnome.org/GNOME/nautilus`), Text Editor
(`gitlab.gnome.org/GNOME/gnome-text-editor`), Fractal
(`gitlab.gnome.org/World/fractal`), Amberol (`gitlab.gnome.org/World/amberol`),
Shortwave (`gitlab.gnome.org/World/Shortwave`), Tuba
(`github.com/GeopJr/Tuba`), Bottles (`github.com/bottlesdevs/Bottles`) and
Workbench (`github.com/workbenchdev/Workbench`).

### Method and limits

Upstream main branches were cloned shallow (`--depth 1`) on 2026-10-19;
the commits above date from 2026-09-29 to 2026-10-19. Each repository was
scanned with `grep`, `find` and direct file reads. Nothing was built, run or
visually compared; this is a source-code benchmark, not a performance or
design audit. Widget counts in the comparison chapter are counts of files
that reference a symbol, not occurrences, and they exclude bundled demos.

Two conventions were verified against the installed libadwaita 1.10 / GTK
4.24 GIR rather than inferred from the apps:

- `AdwApplication` auto-loads `style.css` and `shortcuts-dialog.ui` from the
  resource base path (`Adw-1.gir:7413-7429`).
- `GtkApplication` auto-loads `gtk/menus.ui` from the same path
  (`Gtk-4.0.gir:10230-10238`).

The resource base path defaults to the application ID as a path (for example
`dev.geopjr.Tuba` → `/dev/geopjr/Tuba`); applications can override it at
construction time. The default is derived only when the ID is set during
construction: `Adw.Application(application_id=…)` and a Vala
`construct { application_id = … }` block both yield `/dev/geopjr/Tuba` (tested),
while calling `set_application_id()` afterwards leaves the path `NULL`. This
convention explains why several apps contain no CSS
loader code at all, and why a missing or late base path makes a stylesheet
look dead.

Line references in the project chapters are relative to each repository at
the commit listed above. Main branches include unreleased migrations, so treat
any single file as a work in progress rather than a stable API.

## Nautilus (nautilus) — C + Blueprint (.blp)

### 1. Stack and version baseline

Plain C11 (`c_std=c11`, `warning_level=2`), project version `52.alpha`, Meson `>= 1.1.0` (`meson.build:2-10`). Minimums: `gtk4 >= 4.24.0`, `libadwaita-1 >= 1.8.alpha`, `glib >= 2.89.0`, `blueprint-compiler >= 0.19.0` (`meson.build:108, 128-139`). Nothing in `src/` is version-gated — `grep GTK_CHECK_VERSION|ADW_CHECK_VERSION` yields zero hits; the only shim is a GLib one gated on `!GLIB_CHECK_VERSION (2, 89, 3)` for `g_set_date_time` (`src/nautilus-date-utilities.h:27-30`). App classes are `AdwApplication` / `AdwApplicationWindow` (`src/nautilus-application.h:20`, `src/nautilus-window.h:23`). Commit `8fd4b29261e641d1e6bdb83cc841837d12aefff6`.

### 2. UI architecture and file layout

33 `.blp` files under `src/resources/ui/`, enumerated by hand in `blueprint_names` and compiled in one `custom_target` with `blueprint-compiler batch-compile --minify` (`src/resources/ui/meson.build:1-56`); the file exists separately because "output doesn't accept path separators" (`:45`). Generated `.ui` files are listed explicitly in `nautilus.gresource.xml.in`, compiled with `--manual-register` (`src/resources/meson.build:5-17`).

Two coexisting authoring styles: template classes for reusable widgets (`template $NautilusToolbar: Adw.Bin`, `nautilus-toolbar.blp:4`), and *unrooted* builder objects for one-shot dialogs (`Adw.Dialog select_items_dialog` at `nautilus-files-view-select-items.blp:4`, `Adw.PreferencesDialog` at `nautilus-preferences-dialog.blp:4`, `Adw.Window properties_window` at `nautilus-properties-window.blp:4`), fetched by ID with `gtk_builder_get_object` (`src/nautilus-files-view.c:1779`, `src/nautilus-preferences-dialog.c:219-224`). Context menus are hand-written XML, not Blueprint (`src/resources/menu/*.ui`, loaded at `src/nautilus-files-view.c:8154`, `src/nautilus-pathbar.c:311`).

### 3. Window, navigation and adaptive layout

`window.blp:122-142` nests `Adw.ApplicationWindow` → `NautilusShortcutManager` → `Adw.ToastOverlay` → `Adw.TabOverview` → `Adw.OverlaySplitView`. Not `AdwNavigationSplitView`, and there is no `AdwSidebar`/`AdwViewSwitcherSidebar` anywhere in `src/`: the sidebar is a custom `NautilusSidebar` widget. Content pane: `Adw.ToolbarView` holding `NautilusToolbar`, `Adw.TabBar`, `Adw.TabView`, plus bottom `NautilusNetworkAddressBar` and an `ActionBar` with history controls, view controls and `Adw.TabButton` (`:186-236`).

One breakpoint drives narrow mode: `condition ("max-width: 682sp")` collapses the split view, reveals `toolbar.show-sidebar-button` and `action_bar.revealed`, hides `tab_bar` and the headerbar's history/view controls (`:241-255`). Each tab page is a `NautilusWindowSlot` created in C (`src/nautilus-window.c:417`). Actions are `win.*` entries plus `gtk_application_set_accels_for_action` strings (`src/nautilus-window.c:1070-1110`, `src/nautilus-application.c:110-121`).

### 4. Lists, models, rows and item widgets

`src/nautilus-view-model.c:19-35` documents the chain, built at `:368-375`: `GListStore<ViewItem>` → `GtkFilterListModel` → `GtkTreeListModel` → `GtkSortListModel` → `GtkSelectionModel`. Three leaf views, all `NautilusListBase` (an `AdwBin` that owns a `GtkScrolledWindow` inside a `GtkOverlay`, `src/nautilus-list-base.c:1305-1314`): grid (`gtk_grid_view_new`, `src/nautilus-grid-view.c:500-510`), list (`gtk_column_view_new` + signal factories per column, `src/nautilus-list-view.c:359-390`, `:979-1045`), network (`gtk_list_view_new` with a header factory, `src/nautilus-network-view.c:325-345`). Switching grid/list unparents and recreates the inner view rather than swapping a `GtkStack` (`src/nautilus-files-view.c:9716-9745`).

Cells are `NautilusViewCell` subclasses (a bare `GtkWidget` with an `item` property, `src/nautilus-view-cell.c:20-30`), templated as `$NautilusGridCell: $NautilusViewCell` (`nautilus-grid-cell.blp:3`) and `$NautilusNameCell` with a `TreeExpander` (`nautilus-name-cell.blp:4-30`). Row accessibility is bound through a `GtkExpression` chain up to `NautilusFile:a11y-name`, built in C in the row setup handler (`src/nautilus-list-view.c:345-356`).

### 5. Dialogs, preferences, menus, toasts, banners, status pages

Most dialogs subclass `AdwDialog` and are shown with `adw_dialog_present` (`src/nautilus-new-folder-dialog.c:74`, `src/nautilus-app-chooser.c:89`). Preferences are a singleton `AdwPreferencesDialog` with `search-enabled: true`; its widgets are bound to GSettings by builder name through `g_settings_bind` and `g_settings_bind_with_mapping` (`nautilus-preferences-dialog.blp:4-40`, `src/nautilus-preferences-dialog.c:113-155`). Shortcuts use a static `Adw.ShortcutsDialog` (`shortcuts-dialog.blp:4`); About is `adw_about_dialog_new_from_appdata` (`src/nautilus-window.c:1732`).

Toasts live once at window root and carry undo actions with `ADW_TOAST_PRIORITY_HIGH` (`src/nautilus-window.c:900-953`). Banners are per-slot (`AdwBanner`, `src/nautilus-window-slot.c:864`, driven by `src/nautilus-location-banner.c`). Empty states reuse one kept-alive `AdwStatusPage` whose icon/title/description are rewritten per case (`src/nautilus-files-view.c:9446`, `:3656-3719`). Context menus are rebuilt as a fresh `GtkPopoverMenu` on every popup, explicitly to dodge submenu bugs (`src/nautilus-files-view.c:8206-8243`).

### 6. Styling and resources

A single `style.css` in the gresource (`nautilus.gresource.xml.in:50`) plus custom symbolic icons. It targets the new list widgets structurally (`.nautilus-grid-view gridview > child`, `.nautilus-list-view columnview > listview > row > cell`, `style.css:160-230`) and the app's own classes (`.nautilus-pathbar`, `.nautilus-view-cell`, `.floating-bar`). Dark mode is inherited from libadwaita; only `@media (prefers-contrast: more)` appears (`:43, :83, :136, :211`), with `color-mix()` over libadwaita named colors. Hover/selection accents are overridden per-view (`:353-356`). Real drawing is code: `snapshot()` overrides paint the "cut" ghost with `GtkSvg`, Pango and `AdwStyleManager` high-contrast branches (`src/nautilus-grid-cell.c:425-460`, `src/nautilus-name-cell.c:400-425`).

### 7. Accessibility, keyboard, i18n

Accessible roles are set in class init: `TOOLBAR` (`src/nautilus-toolbar.c:495`), `LIST` for the sidebar (`src/nautilus-sidebar.c:3928`), `SEARCH_BOX` plus CSS name `entry` for the query editor (`src/nautilus-query-editor.c:490-493`), `NONE` for grid cells. Blueprint carries relations and roles: `accessibility { labelled-by: [label] }` (`nautilus-sidebar-row.blp:19`, `nautilus-name-cell.blp:25`) and `accessible-role: checkbox` on filter buttons (`nautilus-search-popover.blp:40`). Keyboard: accelerators are application-level strings; the query editor installs `GtkShortcut`s and `gtk_widget_class_add_binding_signal` (`src/nautilus-query-editor.c:458-462`). `.blp` files are translation sources in `po/POTFILES.in:82ff`, and `_()`/`C_()` work inline (`nautilus-view-controls.blp:24`).

### 8. Distinctive idioms worth copying

- **Shortcut scoping widget**: `NautilusShortcutManager` is an `AdwBin` implementing `GtkShortcutManager`, so `GTK_SHORTCUT_SCOPE_MANAGED` shortcuts stop firing while an `AdwDialog` is up (`src/nautilus-shortcut-manager.c:10-33`). It wraps the window's child in both the main window and the file chooser (`window.blp:134`, `file-chooser.blp:9`).
- **Breakpoint as a template child**: `Adw.Breakpoint breakpoint { condition (...) setters {...} }` bound with `gtk_widget_class_bind_template_child`, then extended from C only when relevant (`src/nautilus-file-chooser.c:790`). Batch rename compares `:current-breakpoint` against the stored breakpoint inside a row-orientation callback (`src/nautilus-batch-rename-dialog.c:1405-1418`, `nautilus-batch-rename-dialog.blp:119-127`).
- **Menus as template children**: `menu view_menu {...}` is bound to a `GMenuModel *` field, then surgically edited — a sort section inserted at a fixed index (`nautilus-view-controls.blp:4-50`, `src/nautilus-view-controls.c:16, 253-258`).
- **Menu filtering by custom attributes**: `show-in-mode` / `show-in-view` are recursively stripped at runtime (`src/nautilus-ui-utilities.c:151-192`).
- **Inline item templates**: `BuilderListItemFactory { template ListItem { ... } }` inside a `MenuButton` popover (`nautilus-progress-indicator.blp:40-70`).
- **Reusable pane components with bindable props**: `NautilusToolbar` exposes `show-view-controls`, `show-history-controls`, `show-sidebar-button` (`src/nautilus-toolbar.c:461-478`), which the file chooser rebinds (`file-chooser.blp:44-50`).

### 9. Anti-patterns / legacy leftovers

The sidebar is 4.2k lines of hand-built C over `GtkListBox`, with a header func for separators, and manual drag state (`src/nautilus-sidebar.c:3492-3539`). A different `GtkListBox`, the progress operations list, carries a CSS hack marked "TODO: Switch to GtkListView and drop this CSS hack" (`style.css:29-35`); the sidebar itself has no such marker. `NautilusWindowSlot`, `NautilusQueryEditor` and `NautilusPathBar` are still constructed in C rather than from templates: plain `GtkBox`/`GtkStack` for the slot and path bar, plus a `GtkBoxLayout` in the query editor (`src/nautilus-window-slot.c:843-880`, `src/nautilus-query-editor.c:490`, `src/nautilus-pathbar.c:280-305`). The location entry keeps `GtkEntryCompletion` (deprecated since GTK 4.10), with a comment that showing only basenames "would take a reimplementation" (`src/nautilus-location-entry.c:233-239`). Duplicated assumptions are documented twice instead of factored: the 682sp condition in two files, and "sort section is the second item" in both `.blp` and C (`nautilus-view-controls.blp:18-21`, `src/nautilus-view-controls.c:16`). Workarounds survive in comments: recreating context popovers each popup, forcing focus back on close, and zeroing `GtkColumnViewCell` padding because "GTK unconditionally sets padding … even with .data-table" (`style.css:223-227`).

### 10. Transferable lessons for a GTK4/libadwaita app

Factor the browsing experience into a standalone slot widget (`NautilusWindowSlot`, an `AdwBin` with a `mode`) so the same sidebar+toolbar+content assembly serves the main window and the file chooser (`src/nautilus-file-chooser.c:764`, `file-chooser.blp:4-47`). Keep one breakpoint per adaptive surface and express narrow-mode changes as setters, not C conditionals. Declare menus once and mutate attributes at runtime rather than rebuilding models in C. Declare accessibility relations (`labelled-by`) in Blueprint where possible; Nautilus builds its list-row name expression in C. Convert old `GtkListBox` panes to `GtkListView` only with matching CSS work, and mark interim CSS hacks with a TODO, as Nautilus does for its operations list. Finally, scope managed shortcuts explicitly — libadwaita dialogs otherwise inherit window shortcuts.

## Text Editor (gnome-text-editor) — C + GtkBuilder XML

### 1. Stack and version baseline

Plain C (`c_std=gnu17`, `meson.build:4`), 18 `.ui` GtkBuilder templates under `src/`, version 51.1 (`meson.build:2`), commit `16542f0`. Dependency floors: GLib 2.80, GTK 4.19.4, libadwaita 1.8.rc, gtksourceview 5.15.0, libspelling 0.4.0 (`meson.build:33-37`). There is no `GTK_CHECK_VERSION`/`ADW_CHECK_VERSION`/`#if GTK` anywhere in `src/`; the project bumps hard floors instead of gating newer APIs. XML declares only gtk 4.0/Adw 1.0 (`editor-window.ui:3-4`).

### 2. UI architecture and file layout

One `.ui` per composite class, registered under `/org/gnome/TextEditor/ui` with `preprocess="xml-stripblanks"` (`org.gnome.TextEditor.gresource.xml:4-20`); `style.css`, `shortcuts-dialog.ui`, icons and GtkSourceView schemes sit under sibling prefixes (`:22-46`). Resources are compiled with `gnome.compile_resources(..., c_name: 'editor')` (`src/meson.build:68-71`).

Each class loads its template in `class_init`, bulk-binds children to struct fields, then calls `gtk_widget_init_template()` first in `init`, followed by per-instance setup (`editor-window.c:921-938, 988-992`):

```c
gtk_widget_class_set_template_from_resource (widget_class, "/org/gnome/TextEditor/ui/editor-window.ui");
gtk_widget_class_bind_template_child (widget_class, EditorWindow, tab_view);
gtk_widget_class_bind_template_callback (widget_class, on_tab_view_close_page_cb);
```

Types named inside `.ui` must be `g_type_ensure`d by the containing class (`editor-page.c:968-972`; `editor-preferences-dialog.c:686-689`). Standalone model objects are declared outside `<template>` in the same file: `EditorIndentModel` (`editor-preferences-dialog.ui:241`), `EditorProperties`/`EditorEncodingModel`/`EditorNewlineModel` (`editor-properties-panel.ui:208-217`).

Wiring is largely declarative: `<binding>`+`<lookup>` chains reach through the window's application (`editor-open-popover.ui:27-37`) or the window's `visible-page` (`editor-window.ui:43-45`). Simple containers are `GtkWidget` + `GTK_TYPE_BIN_LAYOUT` + a CSS name (`editor-position-label.c:57-64`).

### 3. Window, navigation and adaptive layout

`EditorWindow` is an `AdwApplicationWindow` with `width-request` 360 (`editor-window.ui:5-6`). There is no top-level `AdwToolbarView`; the root widget is an `AdwMultiLayoutView` choosing between named `wide` and `narrow` `AdwLayout`s via `AdwBreakpoint`s at 650sp and 400sp (the latter also swaps the Open button's internal stack to a folder icon) (`:9-23, 40, 268, 304`).

Wide: `EditorFullscreenBox` hosts the `header_bar` and `tab_bar` `AdwLayoutSlot`s, content is `AdwOverlaySplitView` with `sidebar-position=end`, `min-sidebar-width=350`, sidebar slot = `EditorPropertiesPanel` (`:283-300`). Narrow: `AdwBottomSheet` whose `open` is bidirectionally bound to the header's properties toggle, reusing the same slots (`:307-336`).

`EditorFullscreenBox` is a GtkWidget implementing GtkBuildable `add_child` for `"top"`/`"bottom"`, internally owning an `AdwToolbarView` with raised bars that auto-hides in fullscreen (`editor-fullscreen-box.c:398-400, 543-556`).

Tabs are stock `AdwTabView` + `AdwTabBar`, with `close-page`, `setup-menu`, `create-window` callbacks (`editor-window.ui:58-70, 263-265`; `editor-window.c:464-488, 622-660`). `adw_tab_view_remove_shortcuts` drops Ctrl+Home/End tab shortcuts (`editor-window.c:1006-1010`); tab bar inversion follows `gtk-decoration-layout` (`:360-386`).

The document page is an `AdwToolbarView` with `bottom-bar-style=raised`, whose bottom child stacks revealers for "Go to Line" and a clamped search bar (`editor-page.ui:13-21, 83-138`). The window title is a custom stacked `GtkCenterBox` (title, subtitle, modified bullet) (`editor-window.ui:103-160`).

### 4. Lists, models, rows and item widgets

Recents use `GtkListView` + `GtkNoSelection` + a `GtkBuilderListItemFactory` whose XML is inlined as CDATA; `EditorSidebarRow` receives `item` through `<binding>` (`editor-open-popover.ui:102-143`). Rows are GtkWidget templates built on `GtkInscription`/GtkGrid, with a tooltip `GtkLabel` declared as a non-widget object in the same template (`editor-sidebar-row.ui:8-101`).

`EditorSidebarModel` is a hand-written `GListModel` over a `GSequence` (`editor-sidebar-model.c:123-124`). Language and encoding pickers use no model at all: rows are appended to `GtkListBox` in `constructed` and filtering walks children toggling `visible` with a `GPatternSpec` glob (`editor-language-dialog.c:101-155, 238-263`).

The properties panel drives `AdwComboRow` with `model` + `expression` lookups and bidirectional `selected` bindings (`editor-properties-panel.ui:75-99`).

### 5. Dialogs, preferences, menus, toasts, banners, status pages

Preferences is an `AdwPreferencesDialog` subclass presented in-window with `adw_dialog_present(dialog, widget)` (`editor-preferences-dialog.ui:6`; `editor-window-actions.c:545-546`). It embeds a live `GtkSourceView` preview and a `GtkFlowBox` of style-scheme previews (`editor-preferences-dialog.ui:17-50`).

Generic rows take a `schema-key`, create their own `GSettings`, wrap `g_settings_create_action` in a `GSimpleActionGroup` named `settings`, and point the internal switch at `settings.<key>` (`editor-preferences-switch.c:86-95`). The window installs its settings actions the same way from a key list (`editor-window-actions.c:740-770`). Confirmations use `AdwAlertDialog` (`editor-window-actions.c:108, 213`); About uses `adw_show_about_dialog` (`editor-application-actions.c:199-218`).

No `AdwBanner` and no `AdwToastOverlay` exist; three legacy `GtkInfoBar`s (access error, restored document, invalid encoding) provide banner-like feedback (`editor-info-bar.ui:9-141`). Menus are inline `GMenu` blocks with `accel` attributes (`editor-window.ui:344-457`), and dynamic widgets are injected by id with `gtk_popover_menu_add_child(..., "theme"/"zoom")` (`editor-window.c:403-452`). Empty states and no-results use `AdwStatusPage` (`editor-window.ui:80-98`, `editor-language-dialog.ui:73`, `editor-encoding-dialog.ui:56`).

### 6. Styling and resources

`style.css` lives at `/org/gnome/TextEditor` (`gresource.xml:22-24`), the app's resource base path (`editor-application.c:636`), and is never loaded explicitly; GTK's application-resource stylesheet convention accounts for it [INFERENCE: no `load_from_resource` call exists in `src/`]. Selectors are scoped by explicit CSS names (`page`, `positionlabel`, `EditorSourceMap`, `editor-page.c:946`) and the window class `org-gnome-TextEditor` (`editor-window.ui:7-9`, `style.css:29, 99`).

Recoloring is a runtime-generated provider loaded with `gtk_css_provider_load_from_string` at `PRIORITY_THEME+1`, regenerated when the GtkSourceView scheme or dark state changes (`editor-application.c:206-224, 342-346`). Per-widget providers still use deprecated `GtkStyleContext` under `G_GNUC_BEGIN_IGNORE_DEPRECATIONS` (`editor-source-view.c:801-810`). CSS uses libadwaita named colors, `color-mix`, `@media (prefers-contrast: more)` and `-gtk-icontheme` (`style.css:12-22, 65-68, 158`). Extra icons are registered as a resource path at startup (`editor-application.c:347-348`).

### 7. Accessibility, keyboard and i18n specifics

Hand-built widgets update accessibility explicitly: accessible labels in templates (`editor-theme-selector.ui:24-26`), `accessible-role=text-box` on a raw `GtkText` (`editor-search-entry.ui:11-12`), role SWITCH plus CHECKED state mirroring on the custom preference row (`editor-preferences-switch.c:58-62, 208`), and zoom-button labels (`editor-window.c:424-427`).

Accelerators are class-wide `gtk_widget_class_add_binding_action` calls with typed targets such as `page.change` + `"i"` + 1..9 (`editor-window.c:945-976`), plus XML `GtkShortcutController`s for F11/Alt+W (`editor-window.ui:24-38`) and a `managed`-scope F9 on the properties toggle (`:248-258`). App-level accels exist only for `app.quit` and `app.help` (`editor-application.c:350-351`). i18n relies on `translatable="yes"` with `context="shortcut window"` on shortcut items (`shortcuts-dialog.ui:8-11`) and `use-underline` mnemonics elsewhere (`editor-window.ui:192`); the gettext domain is declared in the schema (`org.gnome.TextEditor.gschema.xml:1`).

### 8. Distinctive idioms worth copying

Bulk template registration with per-field child binding, then `gtk_widget_init_template` only (`editor-window.c:921-938`). Settings-backed actions generated from schema keys and inserted as a named action group (`editor-window-actions.c:756-769`):

```c
action = g_settings_create_action (settings, key);
g_action_map_add_action (G_ACTION_MAP (group), action);
gtk_widget_insert_action_group (GTK_WIDGET (self), "settings", G_ACTION_GROUP (group));
```

Inline `GtkBuilderListItemFactory` for one-off rows (`editor-open-popover.ui:125-141`), declarative arithmetic-free binding chains for row subtitles (`editor-properties-panel.ui:157-161`), and a custom GtkBuildable exposing typed slots to `.ui` (`editor-fullscreen-box.c:543-556`). Breakpoints only set layout properties.

### 9. Anti-patterns / legacy leftovers / version-gated compromises

- Ctrl+? → `win.alternate-help-overlay` (`editor-window.c:968`) points at an action that does not exist. The bundled `shortcuts-dialog.ui` is live: it sits at the resource base path with id `shortcuts_dialog`, so `AdwApplication` auto-loads it and installs `app.shortcuts` (menu entry at `editor-window.ui:417`). Its `requires lib="libadwaita"` names the wrong library, but GtkBuilder ignores that tag; the file loads once Adw types are registered.
- Dead wrapper: `AdwBreakpointBin` in `editor-search-bar.ui:9` wraps the grid with no `AdwBreakpoint` child. Harmless, but it implies adaptivity that is not there.
- `editor-preferences-row.c:27` registers `AdwActionRow` as `editor_preferences_row`; the file is neither compiled (absent from `src/meson.build`) nor referenced.
- Duplicate `max-children-per-line` property (`editor-preferences-dialog.ui:44-45`); both `<control>` and `<ctrl>` accelerator spellings inside one file (`shortcuts-dialog.ui:12, 23`).
- Deprecated `GtkInfoBar` (since GTK 4.10) remains unguarded; `GtkStyleContext` provider attachment is wrapped in `G_GNUC_BEGIN_IGNORE_DEPRECATIONS` (`editor-preferences-dialog.c:711-720`), and the project builds with `-Wno-deprecated-declarations` (`meson.build:143`), which is how both survive the floor bump.
- A file-static `GSettings *app_settings` lazily initialized in the page (`editor-page.c:60, 980`) instead of application state.

### 10. Transferable lessons for a GTK4/libadwaita app

Build one template class per widget, bundle `.ui` with `xml-stripblanks`, and bind children by field so `init` only needs `gtk_widget_init_template()` plus per-instance state. Use `AdwMultiLayoutView` + breakpoints + `AdwLayoutSlot` to express wide/narrow layouts once. Prefer stock `AdwTabView`/`AdwTabBar` over custom tab chrome. Encode settings→action plumbing as schema-key-driven action groups, and keep GSettings→widget bindings in a `GBindingGroup` rebound per document (`editor-page.c:500-553`). Treat dynamic menu content as `gtk_popover_menu_add_child` ids in the `.ui`, and put custom rows in `GtkBuilderListItemFactory` CDATA. Register `style.css` at the resource base path and scope selectors by CSS name rather than inline style strings; delete dead menus and empty wrappers.

## Fractal (fractal) — Rust (gtk-rs) + Blueprint

### 1. Stack and version baseline

Rust edition 2024, MSRV 1.96 (`Cargo.toml:6-7`). Meson floors are GTK `>= 4.20.2`, libadwaita `>= 1.8.0`, GLib `>= 2.82` (`meson.build:25-28`). The bindings track the floors: `gtk 0.11` with feature `gnome_49` (GTK 4.20, `Cargo.toml:72`) and `adw 0.9` with `v1_8` (libadwaita 1.8, `Cargo.toml:64`; its GTK feature is the lower `gtk_v4_10`). No `check_version` or runtime API probing exists in `src/`; the meson floor is authoritative. 148 `.blp`, 308 `.rs`, ~88.9k Rust LOC. Commit `95622c4`.

### 2. UI architecture and file layout

Layout is one Blueprint per composite class, mirroring the module tree: 147 `gtk::CompositeTemplate` derives and 147 `#[template(resource = "/org/gnome/Fractal/ui/…")]` attributes, one per `.blp` except the standalone shortcuts dialog. Directories are components: `src/session_view/{sidebar,room_history,room_details,explore,…}`, each with `mod.blp` + `mod.rs` plus per-widget `*.blp`/`*.rs` pairs.

Blueprint compilation is a hand-rolled meson dance: a sorted manifest `src/ui-blueprint-resources.in` (148 entries) feeds `custom_target('blueprints')`, emitting every `.ui` into one directory as `<dir>-<name>.ui`, while `alias=` restores the nested resource path (`src/meson.build:8-46`, naming computed at `:19-25`; `build-aux/compile-blueprints.sh` only loops over the compiler). The lone exception, `shortcuts-dialog.blp`, is compiled to the resource root so AdwApplication auto-detects it (`src/meson.build:8-11`).

Rust owns behavior only: templates hold the tree, defaults and bindings; `imp` structs hold `TemplateChild` fields (604 `#[template_child]` occurrences) and signal-handler bookkeeping.

### 3. Window, navigation and adaptive layout

The root is `Adw.ApplicationWindow` → `Adw.ToastOverlay` → `Gtk.Stack` of four `StackPage`s (loading/login/session/error) (`src/window.blp:4-92`). Adaptivity is one boolean: a single `Adw.Breakpoint` at `max-width: 600sp` sets `template.compact: true` (`src/window.blp:94-99`), exposed as an `explicit_notify` property (`src/window.rs:90-91`, setter at `:254`).

`SessionView` (an `Adw.Bin`) hosts `Adw.NavigationSplitView` with `collapsed: bind template.root as <$Window>.compact`, `sidebar: $Sidebar`, `content: $Content` (`src/session_view/mod.blp:15-27`). Rust drives it on selection: `split_view.set_show_content(show_content)` (`src/session_view/mod.rs:211`), and equalizes header-bar heights via `Gtk.SizeGroup` (`:250`). Both panes are `Adw.NavigationPage`s (`src/session_view/sidebar/mod.blp:123`, `src/session_view/content.blp:4`), so the split view needs no manual page management.

`RoomDetails` is an `Adw.PreferencesWindow` whose subpages are `adw::NavigationPage`s pushed with `push_subpage`, cached in a `HashMap<SubpageName, adw::NavigationPage>` to avoid rebuilds (`src/session_view/room_details/mod.rs:110`, `:218-239`). Other adaptivity uses `Adw.BreakpointBin` + declarative breakpoint with `apply`/`unapply` callbacks (`src/session_view/explore/mod.blp:119-122`) or programmatic `adw::Breakpoint` (`src/components/media/audio_player/mod.rs:100-119`).

### 4. Lists, models, rows and item widgets

Twelve `GtkListView`s, one `GridView` (media history) and about 43 `ListBox` declarations across 29 Blueprint files. Two factory styles coexist:

* Rust `SignalListItemFactory`. Room history sets `setup` (deactivatable, unselectable) and `bind`, then a `NoSelection` over a custom `GroupingListModel` (`src/session_view/room_history/mod.rs:342-366`, `:371-372`). Sidebar uses `item.bind_property("item", &row, "item")` in `setup` and treats activation itself (`src/session_view/sidebar/mod.rs:114-131`).
* Declarative `Gtk.BuilderListItemFactory` with an inline `template Gtk.ListItem { … }` and `item: bind template.item` (`src/session_view/room_details/members_page/members_list_view/mod.blp:106-113`), four uses total.

Message rows are *not* created per item. `EventRow` is a `ContextMenuBin` with a single child swapped by event kind, using `child_or_else::<MessageRow>(…)` / `child_or_default::<StateRow>()` (`src/session_view/room_history/event_row.rs:289-300`), backed by a `ChildPropertyExt` trait that replaces the child (`src/utils/mod.rs:597-640`). Item dispatch is a downcast cascade over `Event`, `VirtualItem` and `GroupingListGroup` (`src/session_view/room_history/mod.rs:611-628`).

The sidebar is a flat list of heterogeneous items (sections + rooms) rendered by one `SidebarRow` whose `set_item` downcasts to `SidebarSection` or `Room` (`src/session_view/sidebar/row.rs:151-200`). Custom models replace stock selection: `FixedSelection` implements `gio::ListModel` + `gtk::SelectionModel` and preserves selection across model churn via an equivalence fn (`src/utils/fixed_selection.rs:19-76`).

### 5. Dialogs, preferences, menus, toasts, banners, status pages

An abstract `ToastableDialog` wraps `Adw.ToastOverlay` and exports a `child-content` property (`src/components/dialogs/toastable.rs:18-25`); concrete dialogs subclass the template: `template $AuthDialog: $ToastableDialog { child-content: Gtk.Stack stack {…} }` (`src/components/dialogs/auth/mod.blp:3-12`). Account settings is an `Adw.PreferencesDialog` whose page children are bound in the template (`src/account_settings/mod.blp:4-26`); `RoomDetails` keeps the deprecated `PreferencesWindow`, reason documented inline (`src/session_view/room_details/mod.rs:1-3`) and special-cased in the toast walker (`src/utils/toast.rs:171-178`).

Menus are top-level `menu` blocks in the same `.blp`, referenced by `menu-model:` (`src/session_view/room_history/mod.blp:4-38`, `src/session_view/sidebar/mod.blp:5-25`) with `menu-model:`; a `TemplateChild<gio::MenuModel>` for the room-row menu is applied from Rust (`src/session_view/sidebar/mod.rs:61`, `:400`).

The `toast!` macro (175 call sites) walks ancestors for a `ToastableDialog`, `AdwPreferencesDialog`, `PreferencesWindow` or `Window`, panicking otherwise (`src/utils/toast.rs:159-183`); it interpolates `{name}` and embeds `Pill` widgets in the toast title (`:193-260`). Three `Adw.Banner`s (call room, verification, pending knocks) and `Adw.StatusPage`s in 15 Blueprint files cover inline status.

### 6. Styling and resources

There *is* custom SCSS — 1,202 lines across `style.scss` plus seven partials (`data/resources/stylesheet/`) — but almost none is structural chrome. No `CssProvider` is registered in Rust; the compiled `style.css` sits at the gresource root (`data/resources/resources.gresource.xml:97`) and is auto-loaded by AdwApplication from the resource base path. Dark mode and high contrast come from Adwaita; `adw::StyleManager` is consulted only for the GtkSourceView style scheme (`src/utils/sourceview.rs:8,23`) and the audio waveform's high-contrast check (`src/components/media/audio_player/waveform.rs:190`).

Dominant idiom is CSS names, not class soup: 47 `klass.set_css_name("…")` calls (`event-row` `src/session_view/room_history/event_row.rs:44`, `room-title` `title.rs:41`, `sidebar` `sidebar/mod.rs:89`) matched by element selectors in SCSS. `_vendor.scss:1-46` re-copies libadwaita 1.6.1 color math and the focus-ring mixin, with contrast-sensitive CSS variables. Custom symbolic icons register through `IconTheme` resource paths (`src/main.rs:78-79`).

### 7. Accessibility, keyboard, i18n

81 `accessibility { … }` blocks and 110 `accessible-role` assignments in Blueprint; roles not expressible there are set in `class_init` (`gtk::AccessibleRole::Group`, `src/session_view/room_history/mod.rs:149`; `ListItem` at `event_row.rs:45`). `grab_focus` is overridden to route focus into the visible stack page (`src/window.rs:242-249`, `src/identity_verification_view/mod.rs:86-102`). Keyboard: declarative `Gtk.ShortcutController` with capture phase (`src/session_view/mod.blp:31-53`) plus `add_binding_action` in `class_init` (`src/window.rs:113-114`). i18n is `_()` in Blueprint with 20 `C_()` context markers, and `gettext_f`/`ngettext_f` with `{name}` substitution for runtime strings (`src/i18n.rs:4-19`, `src/utils/mod.rs:80`).

### 8. Distinctive idioms worth copying

Shared, reusable template callbacks callable from any `.blp`:

```
$logical_or(template.room as <$Room>.has-notifications, $invert_boolean(…) as <bool>)
$ternary_string(template.room as <$Room>.has-notifications, "count", "dot") as <string>
```
(`src/session_view/sidebar/room_row.blp:36-40`, callbacks at `src/utils/template_callbacks.rs:8-57`).

`BoundObject<T>`: a property type holding the object *and* its handler IDs, disconnecting on replace/drop, with `glib::property::Property`/`PropertyGet` impls so `#[property(get, set = …)]` just works (`src/utils/mod.rs:117-183`, `:243-263`). Setters do an equality guard, then `explicit_notify` + manual `notify_*` (`src/window.rs:254-262`). Row actions are GActionGroups inserted under a namespace so menus work with `hidden-when: action-missing` (`src/session_view/sidebar/row.rs:285-303`). One `.blp` encodes a seven-state button by mapping a Rust enum to stack page names (`src/components/action_button.blp:9-125`, `src/components/action_button.rs:18-31`).

### 9. Anti-patterns / legacy leftovers / version-gated compromises

Deprecated `AdwPreferencesWindow` is retained for `RoomDetails` (media-viewer constraint) and special-cased in the toast walker. Signal bookkeeping is manual and verbose: 546 `clone!` sites and per-signal `RefCell<Option<SignalHandlerId>>` fields (`src/session_view/room_history/event_row.rs:20-35`) — the main per-component cost. `add_toast` panics when the widget's root is an unsupported window type and silently drops the toast when it is unrooted (`src/utils/toast.rs:170-182`). SCSS depends on the GTK list node layout (`.room-history .room-history-list > row`, `_room_history.scss:46-58`, a public CSS node) and on libadwaita 1.6.1 colour math and focus-ring mixins copied into `_vendor.scss`. 147 hand-written `imp` scaffolds repeat themselves with no project-specific macro.

### 10. Transferable lessons for a GTK4/libadwaita app

1. One `.blp` per class with the resource path mirroring the source path keeps 147 components navigable; the flat-output + `alias` trick is what lets a single meson `custom_target` work.
2. Keep Blueprint declarative: typed property bindings (`as <$Type>`) plus a global callbacks struct remove most Rust glue.
3. Model over widget layer — `FixedSelection`, `GroupingListModel` and a `NoSelection` view let a chat log be a real virtualized list, not a `ListBox` of widgets.
4. Represent adaptivity as one window boolean bound into `Adw.NavigationSplitView.collapsed`; use `BreakpointBin` only for self-contained widgets.
5. Narrow composite `Bin`s (`ActionButton`, `CopyableRow`, `Avatar`) are the vocabulary; in-place child replacement reuses the child widget when the event type is unchanged on rebind.
6. Cross-cutting UI (`toast!`, actions, shared callbacks) belongs in `utils/`, not per component.

## Amberol (amberol) — Rust (gtk-rs) + Blueprint

### 1. Stack and version baseline

Rust 2018 (`Cargo.toml:8`), Blueprint compiled to GtkBuilder XML, GStreamer playback. Meson requires `gtk4 >= 4.19.5` and `libadwaita-1 >= 1.8.rc` (`meson.build:11-12`), while the bindings advertise older API levels: `gtk4` feature `v4_16` (`Cargo.toml:14`) and `libadwaita 0.9` feature `v1_5` (`Cargo.toml:32-35`). Commit `003cbf2b794fa62f0b5c4e321d4bf50ef91efbc3`. No `#[cfg]` or version checks gate UI APIs in `src/`; meson sets a configure-time floor (the Flatpak manifest tracks `runtime-version: "master"`), and crate features bound which Rust symbols are visible.

### 2. UI architecture and file layout

Eight Blueprints in `src/gtk/` are batch-compiled by meson (`src/gtk/meson.build:4-22`):

```
find_program('blueprint-compiler'), 'batch-compile',
'@OUTPUT@', '@CURRENT_SOURCE_DIR@', '@INPUT@',
```

Each `.ui` binds to one Rust subclass via `#[template(resource = "/io/bassi/Amberol/<name>.ui")]` plus `init_template()` (`src/playlist_view.rs:10-52`); widgets are exposed as `TemplateChild<T>`. `src/amberol.gresource.xml` bundles icons under `.../icons/scalable/actions/` and all templates plus `style.css` under `/io/bassi/Amberol` (`src/amberol.gresource.xml:22-32`). `main.rs:56-74` loads the `.gresource` by hand; the application sets `resource-base-path` (`src/application.rs:121-124`). That base path is load-bearing: libadwaita auto-loads `<base>/style.css` and `<base>/shortcuts-dialog.ui`, and indeed no code calls `CssProvider::load_from_resource` or installs `app.shortcuts`. The only manual provider serves the dynamic palette at priority 400 (`src/window.rs:1399-1404`).

### 3. Window, navigation and adaptive layout

`src/gtk/window.blp` is a sandwich `WindowHandle → $DragOverlay → Adw.ToastOverlay → Stack`, with `initial-view` and `main-view` pages (`window.blp:13-20`). Main is `Adw.OverlaySplitView` with `max-sidebar-width: 330`, `sidebar-width-fraction: 0.4` (`window.blp:83-86`); content is an `Adw.ToolbarView` around a centered `Box`. No `Adw.NavigationView`/`ViewSwitcher` exists.

Adaptivity is declared, not coded. The one breakpoint sits at template top level:

```
Adw.Breakpoint {
  condition ("max-width: 560sp")
  setters { split_view.collapsed: true; }
}
```
(`window.blp:171-177`)

Code only reacts: a `collapsed` notify reveals the playlist header's back button (`src/window.rs:861-874`; the button starts `visible: false` in `playlist-view.blp:42-48`). `queue.toggle` is a property action on `playlist-visible` (`window.rs:212`) that writes `split_view.set_show_sidebar()` (`window.rs:433-437`); `show-sidebar` notify mirrors state back (`window.rs:876-885`). `Adw.Clamp` (`maximum-size: 450`) guards only the empty state (`window.blp:30-34`); the playback column uses fixed `width-request: 256` instead of a second breakpoint.

### 4. Lists, models, rows and item widgets

A `GtkListView` in a `ScrolledWindow`, styled `navigation-sidebar`, `single-click-activate` (`playlist-view.blp:84-97`). The model chain is built in code: `Queue.model()` (`gio::ListStore` inside `ShuffleListModel`, `src/audio/queue.rs:19-32`) → `FuzzyFilter` → `FuzzySorter` → `NoSelection` (`window.rs:1152-1160`). Both filter and sorter are GObject subclasses (`src/search.rs:25-68`, `src/sort.rs:25-72`).

The factory does not subclass `GtkListItem`; it instantiates a composite `QueueRow` and binds seven properties (`window.rs:1096-1149`):

```
list_item.bind_property("item", &row, "song").sync_create().build();
list_item.property_expression("item")
    .chain_property::<Song>("title")
    .bind(&row, "song-title", gtk::Widget::NONE);
```

`QueueRow` declares `song`, `song-artist`, `song-title`, `song-cover`, `playing`, `selection-mode`, `selected` (`queue_row.rs:80-90`) and swaps two whole `Stack` pages (`song-details` / `selection-mode`) in `update_mode` (`queue_row.rs:197-208`), duplicating labels per page (`queue_row.blp:53-69`, `110-127`). Labels are `GtkInscription` with `text-overflow: ellipsize_end` (`queue_row.blp:53-69`); a nested `Stack` swaps a symbolic placeholder for `$AmberolCoverPicture` (`queue_row.blp:12-45`).

### 5. Dialogs, preferences, menus, toasts, banners, status pages

There is no preferences window; settings are menu toggles (`win.enable-recoloring`, `app.background-play` — `playback-control.blp:181-188`) stored in GSettings (`data/io.bassi.Amberol.gschema.xml:15-24`). The menu is a Blueprint `menu primary_menu` on a `MenuButton` with `direction: up` (`playback-control.blp:127-143`, `149-230`), with a ReplayGain submenu. File selection uses async `GtkFileDialog` (`window.rs:158-195`). `shortcuts-dialog.blp:7` declares `Adw.ShortcutsDialog shortcuts_dialog`; libadwaita turns it into `app.shortcuts`. Toasts are code-only, including one with a button and action target (`window.rs:1506-1517`):

```
toast.set_button_label(Some(&button));
toast.set_action_name(Some("win.skip-to"));
toast.set_action_target_value(Some(&pos.to_variant()));
```

`Adw.StatusPage` appears in the empty state with pill buttons (`window.blp:35-75`) and in the drag overlay. Selection mode uses a `GtkActionBar` at `[bottom]` (`playlist-view.blp:100-125`). No `Adw.Banner` is present.

### 6. Styling and resources

Every custom widget declares a CSS element name, so `style.css` targets elements rather than classes: `playlistview`, `queuerow`, `songcover`, `songdetails`, `playbackcontrol`, `volume`, `waveformview`, `dragoverlay`, plus `picture`/`label` for drawing primitives (`playlist_view.rs:45`, `queue_row.rs:58`, `cover_picture.rs:50`, `marquee.rs:46`). Rules mix layout classes (`main-box`, `large`) with descendant selectors (`songcover picture.cover`, `queuerow label.song-title`) — `style.css:1-90`.

Dark mode is a `@media (prefers-color-scheme: dark)` block redefining custom properties (`style.css:146-157`). The window background is a three-layer gradient driven by `--background-color-{0,1,2}` extracted from cover art; the window injects them as a `:root` block through its provider (`window.rs:1406-1450`) and clears the provider plus `.main-window` class when recoloring is off (`window.rs:1409-1413`). Drawing is real GSK: cover art center-crops by aspect ratio with `append_scaled_texture` (`cover_picture.rs:121-151`); the waveform paints 2px bars colored from `style_context().color()` (deprecated since GTK 4.10 in favour of `Widget::color()`) and `adw::StyleManager::is_high_contrast()` (`waveform_view.rs:172-199`); the marquee masks and clips a snapshot (`marquee.rs:150-230`). Symbolic SVGs are aliased into the resource path (`amberol.gresource.xml:3-21`).

### 7. Accessibility, keyboard, i18n specifics

Blueprints carry inline `accessibility { label: C_("a11y", …) }` (`window.blp:127-135`, `playback-control.blp:27-45`), and composite wrappers set group roles (`playlist_view.rs:46`). The waveform uses `AccessibleRole::Slider`, implements `focus()` to release arrow keys at the ends (`waveform_view.rs:65`, `131-149`), and cover images update an accessible label in `constructed` (`cover_picture.rs:60-76`). Accelerators are registered centrally (`application.rs:66-80`: `<primary>p`, `F9`, `<primary>F`) while the shortcuts dialog lists the same action names. Search calls `SearchBar::set_key_capture_widget` on the window (`window.rs:1031-1034`).

Translation is gettext: blueprints use `_()`/`C_()`, Rust uses `i18n`/`ni18n_k`/`i18n_k` with named `{title}` placeholders (`src/i18n.rs:40-90`; `window.rs:1519-1529`), and `main.rs:41-48` binds the domain.

### 8. Distinctive idioms worth copying

- **Declare adaptation, react in one handler.** The breakpoint sets `collapsed`; one notify shows the back button (`window.rs:861-874`).
- **Register custom types before inflation.** `CoverPicture::static_type()` / `VolumeControl::static_type()` run in `instance_init` so `<AmberolCoverPicture>` resolves (`song_cover.rs:36-39`, `playback_control.rs:57-60`).
- **Factory bindings as expressions, not row code** (`window.rs:1125-1144`).
- **Property actions for toggles**: `install_property_action("queue.toggle", "playlist-visible")` (`window.rs:212-215`) keeps toggle state in the property rather than in a handler.
- **A container owning its drop target**: `DragOverlay` takes a `drop-target` property and toggles a `Revealer` plus `blurred` class from `current-drop` (`drag_overlay.rs:70-84`, `122-140`).

### 9. Anti-patterns / legacy leftovers / version-gated compromises

- CSS reaches into libadwaita internals: `.main-window > overlay-split-view > widget.background > playlistview` (`style.css:126`) depends on libadwaita's internal node names and may break if they change.
- Size configuration contradicts itself: `width-request: 360`, `height-request: 660`, `default-width: 800` (`window.blp:9-11`) versus GSettings defaults 600×300 restored at startup (`window.rs:410-414`): the stored 600 width always overrides the 800 default, and the stored 300 height is below `height-request: 660`, so 660 wins.
- Cargo features (`v4_16`, `v1_5`) sit below the meson floor (`4.19.5`, `1.8.rc`). Not a defect today: no Rust code uses API above those levels, and the 1.8 requirement comes from the Blueprint-only `Adw.ShortcutsDialog`. It would only bite when Rust code needs a newer symbol.
- `.main-window` is added to and removed from `main_stack`, a content stack, not the window (`window.rs:1411`, `1437-1439`), making the name misleading.
- Row labels are duplicated across stack pages instead of varying one page (`queue_row.blp:91-142`).
- The empty-state icon is set manually, with a comment about avoiding build-time generation, although seven other templates are generated (`window.rs:1061-1064`).

### 10. Transferable lessons for a GTK4/libadwaita app

Put every reusable view in a Blueprint template with one Rust subclass, keep policy in the window, and let libadwaita auto-load `style.css` and `shortcuts-dialog.ui` from the resource base path instead of writing loaders. Express adaptation as `Adw.Breakpoint` setters and limit code to mirroring derived state (`collapsed` → back button → `show-sidebar`). Implement your own `GtkFilter`/`GtkSorter` and drive item widgets through property expressions plus small declared GObject properties. Name custom widgets with `set_css_name`, and reserve one dynamic provider (priority 400) for per-item variables; paint custom content with `GtkSnapshot`, taking the foreground colour from `Widget::color()` (4.10+; Amberol itself still uses the deprecated `style_context().color()`) and the high-contrast state so themes keep working. Finally, keep non-UI subsystems behind a trait (`audio/controller.rs:6-12`) fed by a channel, so the UI imports neither MPRIS nor GStreamer types (`audio/mod.rs:15-38`).

## Shortwave (shortwave) — Rust (gtk-rs) + GtkBuilder XML (.ui)

### 1. Stack and version baseline

Rust, edition 2024 (`Cargo.toml:5`), gtk4-rs `0.11` (`gnome_47`) and libadwaita-rs `0.9` (`v1_7`) (`Cargo.toml:43,48`). Meson floors: `gtk4 >= 4.16`, `libadwaita-1 >= 1.8.alpha`, `glycin-gtk4-2 >= 2.1.0` (`meson.build:16-18`). Commit `f417e1a`; 22 `.ui` files and 19 `#[template(resource = ...)]` templates. No `cfg(feature)` gate exists in `src/`: the binding level (1.7) sits below the runtime floor (1.8.alpha) because 1.8 widgets are used only declaratively — `AdwShortcutsDialog` (libadwaita 1.8 per installed `Adw-1.gir`) appears solely in `data/gtk/shortcuts_dialog.ui:3`.

### 2. UI architecture and file layout

Hand-written XML in `data/gtk/`, explicitly listed in `data/de.haeckerfelix.Shortwave.gresource.xml.in` with `preprocess="xml-stripblanks"` under prefix `/de/haeckerfelix/Shortwave`; two files are aliased to framework names (`shortcuts-dialog.ui`, `style.css`). Meson builds the bundle (`data/meson.build:115-123`); Rust loads the installed `.gresource` at startup (`src/main.rs:115-120`), with paths from compile-time `MESON_*` vars (`src/config.rs:4-16`) — no `build.rs` or `glib-build-tools`. Every widget: `CompositeTemplate` + `#[template(resource=...)]`, typed `#[template_child]`, `bind_template` in `class_init`, `init_template()` in `instance_init` (`src/ui/window.rs:36-63, 155-157`). Template `class=` equals the Rust GObject name, so custom widgets nest without registration; the only manual touch is `SwStationRow::static_type()` before installing a factory (`library_page.rs:94-95`).

### 3. Window, navigation and adaptive layout

`window.ui` stacks the normal UI against a `GtkWindowHandle`-wrapped `SwPlayerGadget` mini-player (`window.ui:92`); gadget mode is size-triggered, not code-triggered: an `AdwBreakpoint` with `max-height: 165px` sets `stack:visible-child` (`window.ui:13-18`), and Rust only resizes the window (`window.rs:287-316`). `AdwMultiLayoutView` (`window.ui:26`) holds `wide` = `AdwOverlaySplitView` (sidebar end, 300–350 px, gestures off, `:31-49`) and `narrow` = `AdwBottomSheet` (`SwPlayerToolbar` bottom bar, `player_view` sheet, content `margin-bottom` bound to `bottom-bar-height`, `:55-70`). A `max-width: 625px` breakpoint switches `layout-name` and hides the gadget button (`:7-11`). The center slot is an `AdwNavigationView` (`:75-83`). No `AdwNavigationSplitView` exists. Navigation is tag-based: pages declare `tag`; buttons use `navigation.push` with `'search'` (`library_page.ui:11-12`), and `window.search` calls `push_by_tag` (`window.rs:130-132`); `stop-search` pops (`search_filter.rs:84-86`).

### 4. Lists, models, rows and item widgets

`GtkGridView` (`max_columns=3`, `min_columns=1`, `single_click_activate`) inside `AdwClampScrollable` `maximum_size=1600` (`library_page.ui:41-48`, `search_page.ui:139-147`). The factory is inline CDATA on `GtkBuilderListItemFactory`, binding the custom row: `<binding name="station"><lookup name="item">GtkListItem</lookup>` (`library_page.ui:50-61`, duplicated in `search_page.ui:149-160`). No `GtkListView`, `GtkColumnView`, `SignalListItemFactory` or `GtkFilterListModel`. Discover uses `GtkFlowBox` + `bind_model` with a manual `GtkFlowBoxChild` (`search_page.rs:80-104`). Boxed lists are `GtkListBox` + `boxed-list`, populated through `bind_model` creating `SwTrackRow`s (`player_view.ui:291`, `player_view.rs:86-89`). Models are hand-written: `SwStationModel` implements `gio::ListModel` over `IndexMap` (`station_model.rs:29-58`, updates `:70-85`); sorting is a custom `gtk::Sorter` with properties bound to page properties (`station_sorter.rs:48-75`) inside `SortListModel` + `NoSelection` (`library_page.rs:92-99`). Filtering is server-side only (`search_page.rs:213-240`). Rows are `AdwBin`/`AdwActionRow` subclasses exposing the model item as a property.

### 5. Dialogs, preferences, menus, toasts, status pages

Dialogs are `AdwDialog` templates with fixed `content_width=500`; station, track and device dialogs wrap their content in their own `AdwToastOverlay` (`station_dialog.ui:3-9`), the add-station and preferences dialogs do not, and a window-level overlay (`window.ui:24`) is the default sink for `DisplayError`. Dialogs are presented via `present(Some(win))` and deduplicated by downcasting `visible_dialog()` (`window.rs:84-92`). Preferences: `AdwPreferencesDialog` (`preferences_dialog.ui:3-8`), direct GSettings property binds (`preferences_dialog.rs:66-119`), radio semantics via a string-targeted property action in a `"player"` action group (`:79-82`, `preferences_dialog.ui:74-106`). No `AdwBanner` usage. Menus are `<menu id=...>` siblings in the same file (`library_page.ui:111-183`); a global menubar in `data/gtk/menus.ui` is auto-loaded by `GtkApplication` from `gtk/menus.ui` (`Gtk-4.0.gir:10233`). `DisplayError` turns a `Result` into a toast whose button opens an `AdwAlertDialog` (`display_error.rs:45-75`). Status pages are named `AdwViewStackPage`s (`search_page.ui:178-227`, spinner via `AdwSpinnerPaintable` `:181-186`); empty library at `library_page.ui:79-105`.

### 6. Styling and resources

`data/gtk/style.css` is aliased to `style.css` and auto-loaded by `AdwApplication` (`Adw-1.gir:7428`); no `CssProvider` in `src/`. Custom widgets set element names: `cover` (`station_cover.rs:53`), `volume` (`volume_control.rs:55`), `device-indicator` (`device_indicator.rs:52`), enabling selectors like `volume > scale trough slider` (`style.css:116-123`). Dark mode is `@media (prefers-color-scheme: dark)` (`style.css:137-141`); colors use `color-mix()`/`var()` (`:44-47,70`). The gadget's wood/metal look is CSS over bundled PNGs (`style.css:31-63`). Custom drawing exists only in `SwScalableImage::snapshot` (`scalable_image.rs:59-66`); covers load through glycin while mapped (`station_cover.rs:65-137`).

### 7. Accessibility, keyboard, i18n specifics

Four `accessible_role=presentation` marks, one `<accessibility>` label for the volume scale (`volume_control.ui:20-22,29`), and `set_accessible_role(Group)` (`volume_control.rs:56`). Commands are GActions with accels using a platform `Primary`/`Meta` prefix (`app.rs:144-158`); the shortcuts window is declared in `shortcuts_dialog.ui` and exposed as `app.shortcuts` by libadwaita. Search grabs focus on `shown()` (`search_page.rs:145,160`). i18n: 146 `translatable="yes"` properties, `context=` on shortcut items, `comments=` for ambiguous nouns (`preferences_dialog.ui:43`), `ngettext` (`preferences_dialog.rs:162-174`), `formatx!` `{}` placeholders (`station_dialog.rs:206`).

### 8. Distinctive idioms worth copying

Nested `<lookup>` chains encode view logic declaratively, e.g. a label bound to `player.station.title` with explicit `type=` hints (`player_view.ui:64-70`); breakpoints set ordinary properties (`layout-name`, `show-gadget-button`), keeping adaptivity data-only; one trait reports all errors (`display_error.rs:29-75`); framework conventions replace loader code.

### 9. Anti-patterns, legacy leftovers, version-gated compromises

- `data/gtk/player.ui` ships (`…gresource.xml.in:14`, `po/POTFILES.in:10`) but no template references it.
- Two property-binding syntaxes are mixed: 32 expression `<binding>` blocks across 9 files and three `bind-source` attributes (`add_station_dialog.ui:39`, `track_dialog.ui:115`, `window.ui:62`). Both are supported GtkBuilder features.
- `player_view.ui:5` is an `AdwBreakpointBin` with no breakpoints.
- Grid column range is capped at 3 (min 1) inside a 1600-wide clamp, so large windows gain gutters rather than columns.
- `SwSearchFilterItem` is fully imperative with empty `class_init`/`instance_init` (`search_filter_item.rs:41-60`); `add_filter` inserts a hardcoded `"foo"` chip (`search_filter.rs:72-76`) and `has_filter()` reads only the entry (`:104-106`).
- Cargo enables libadwaita-rs `v1_7` while Meson requires 1.8.alpha. This only limits the Rust symbols in scope; no Rust code in the chapter needs newer API.

### 10. Transferable lessons for a GTK4/libadwaita app

Class-name matching makes `.ui` + gtk-rs fully typed, custom widgets included. Keep adaptive structure in XML (`AdwMultiLayoutView` plus breakpoint setters). `<binding>` chains delete notify code but repeat: the identical factory block is duplicated and `player_view.ui` carries ten chains; Blueprint's one-line bindings would shrink this, at the cost of a codegen step. Prefer convention-named resources over loader code, and grep template paths in CI — dead `.ui` files are invisible to the compiler.

## Tuba (tuba) — Vala + GtkBuilder XML

### 1. Stack and version baseline

Vala (245 `src/**/*.vala` files) with GtkBuilder templates: 40 `.ui` files, of which 24 files declare 26 `[GtkTemplate]` classes. Meson project version 0.11.1 (`meson.build:3-4`). Minimums: GTK `>=4.19` (`meson.build:104`) and libadwaita `>=1.8` (`meson.build:105`); libsoup 3, GtkSourceView 5, gee json-glib are hard deps (`meson.build:182-192`). Commit `02305ed00594ad87095b13f9c273cf842d3adc5b` (2026-09-29).

GTK 4.20/4.22 APIs are gated with defines generated from pkg-config (`meson.build:126-132`), used for `Gtk.Calendar.date` (`src/Dialogs/Composer/Schedule.vala:55-60`) and `Gtk.Snapshot.push_isolation` (`src/Widgets/FocusPicture.vala:130-132`). Libadwaita has **no** gates: `Adw.WrapBox`, `Adw.Dialog`, `Adw.OverlaySplitView` and `SP` breakpoints are used unconditionally.

### 2. UI architecture and file layout

UI XML mirrors the Vala tree: `data/ui/dialogs/`, `data/ui/views/` (+ `views/sidebar/`), `data/ui/widgets/`, plus single-purpose fragments in `data/gtk/dropdown/`. Everything is bundled under `/dev/geopjr/Tuba/` (`data/gresource.xml.in:3-4`). Templates point at resource paths, not relative files:

```vala
[GtkTemplate (ui = "/dev/geopjr/Tuba/ui/widgets/status.ui")]
public class Tuba.Widgets.Status : Gtk.ListBoxRow {
    [GtkChild] protected unowned Gtk.Box status_box;
```
(`src/Widgets/Status.vala:1-2,110`)

Signal handlers are declared in XML with `swapped="no"` and implemented as `[GtkCallback]` methods (`data/ui/widgets/status.ui:209`, `src/Widgets/Status.vala:1571`). `static construct` force-registers nested template types (`src/Widgets/Status.vala:188-194`, `src/Dialogs/MainWindow.vala:22-25`) because templates reference `TubaWidgetsAvatar`/`TubaWidgetsMarkupView` before those classes are touched. Fragments under `data/gtk/dropdown/` (12 files, 10 of them dropdown factories) feed `Gtk.BuilderListItemFactory.from_resource`, so composite rows need no Vala class (`src/Dialogs/Composer/Dialog.vala:288-289`). Menu models are XML (`data/ui/menus.ui`), loaded via `Gtk.Builder.from_resource` (`src/Views/Profile.vala:485-487`).

### 3. Window, navigation and adaptive layout

The main window is a three-layer nest: `AdwToastOverlay` > `GtkOverlay` (media viewer) > `AdwOverlaySplitView` (sidebar / `AdwNavigationView`) (`data/ui/dialogs/main.ui:13-29`). Adaptation is expressed in XML:

```xml
<object class="AdwBreakpoint" id="breakpoint">
  <condition>max-width: 700sp</condition>
  <setter object="split_view" property="collapsed">true</setter>
</object>
```
(`data/ui/dialogs/main.ui:7-10`)

The breakpoint is extended in code with `breakpoint.add_setter (app, "is-mobile", true)` (`src/Dialogs/MainWindow.vala:33`); that global flag drives avatar sizes, back buttons and tab bars. Navigation is manual: sidebar items `replace()` with `[main_page, page]`, others `push()` (`src/Dialogs/MainWindow.vala:201-208`), and a collapsed split view hides its sidebar (`src/Views/Sidebar.vala:279-281`). Home is a `TabbedBase`: `Adw.ViewStack` with three `ContentBase`s (`src/Views/Main.vala:5-9`), wide `Adw.ViewSwitcher` in the header and `Adw.ViewSwitcherBar` at ≤550sp (`src/Views/TabbedBase.vala:83-107`). Nested breakpoints set custom booleans: 670px→`small` (`src/Views/Base.vala:132-137`), 430sp→`is-narrow` in the composer (`src/Dialogs/Composer/Dialog.vala:393-400`), 300/380sp in post action rows (`src/Widgets/Status/ActionsRow.vala:225-231`). `Adw.NavigationSplitView` appears only in the admin dashboard at 400sp (`data/ui/dialogs/admin_dashboard.ui:10-16`).

### 4. Lists, models, rows and item widgets

`ContentBase` owns a `GLib.ListStore` of `Widgetizable` and binds it to a `Gtk.ListBox` (`src/Views/ContentBase.vala:13,32-38`). Entities themselves are model items and know how to become widgets:

```vala
public override Gtk.Widget to_widget () {
    return new Widgets.Status (this);
}
```
(`src/API/Status.vala:286-288`)

The box carries `css_classes = { "fake-content", "background" }` to dodge Gtk's `.content` class (`src/Views/ContentBase.vala:34`). `on_create_model_widget` adds `card`/`card-spacing` and focusability, then profile strips them and wraps the cover in a non-activatable row (`src/Views/Profile.vala:316-335`). Sidebar uses composed models — `Gtk.SliceListModel` (capped at 25/15 items) flattened by `Gtk.FlattenListModel`, bound to a `Gtk.ListBox` (`src/Views/Sidebar.vala:3-4,110-120`). Rows are custom `Gtk.ListBoxRow` subclasses binding to `Place` objects (`src/Views/Sidebar.vala:231-271`). Attachments are a `Gtk.FlowBox`, one column with `height_request = 334` for a single attachment, two columns otherwise (`src/Widgets/Attachment/Box.vala:62-70,131,146-155`). Recycled views exist only in the file drive (`Gtk.GridView`, `src/Views/Drive.vala:870-882`) and emoji picker (`src/Widgets/CustomEmojiChooserV2.vala:104-112`).

### 5. Dialogs, preferences, menus, toasts, banners, status pages

The composer is an `AdwDialog` carrying its **own** `AdwToastOverlay` and `AdwNavigationView` (`data/ui/dialogs/composer.ui:5,13-15`). Preferences is an `AdwPreferencesDialog` with `search-enabled` and plain `AdwSwitchRow`/`AdwActionRow` groups (`data/ui/dialogs/preferences.ui:12,19-27`). Confirmation dialogs are centralized behind a coroutine helper returning an enum (`src/Application.vala:981-1010`).

Empty/loading/error states are **not** `Adw.StatusPage` for timelines: `base.ui` has a `GtkStack` with spinner and message pages plus a reload button (`data/ui/views/base.ui:46-140`, driven by `base_status` in `src/Views/Base.vala:89-125`). `Adw.StatusPage` appears only in dialogs (`src/Dialogs/Report.vala:230`, `ProfileLists.vala:95`, `NewAccount.vala:35`, `Composer/DropOverlay.vala:48`). Toasts go through an app signal into the window overlay (`src/Dialogs/MainWindow.vala:39,50-58`), though search/admin create local overlays (`src/Views/Search.vala:419`). Context menus for posts are assembled in code with `Gtk.PopoverMenu.from_model` (`src/Widgets/Status.vala:384-481`); profile menus come from XML and use `display-hint=circular-buttons` (`data/ui/menus.ui:7`). Sidebar `AdwBanner`s report offline, unread announcements and follow requests (`data/ui/views/sidebar/view.ui:69-91`).

### 6. Styling and resources

A single 1347-line `data/style.css` sits at the app resource base path (`data/gresource.xml.in:4`). No provider is created for it: libadwaita auto-loads `style.css` from the `GApplication` resource base path; the only explicit provider is for easter-egg CSS (`src/Application.vala:1120-1123`). The sheet uses Adwaita named variables (`var(--card-bg-color)`) and no `@define-color`; dark and high-contrast tuning use media queries (`data/style.css:1168`, `:1263`) with a "wait for light-dark()" comment (`:1169`).

Class names are prefixed `ttl-` and often coupled to the widget tree: `.ttl-view:not(.large-view)>.fake-content>.card` (`data/style.css:552-557`). Post font scaling is 12 hard-coded classes (`data/style.css:1301-1345`) toggled from Vala (`src/Widgets/Status.vala:178-186`) via `Tuba.toggle_css` (`src/Application.vala:1129-1134`). Custom drawing is real `snapshot()` work: fade-out long posts (`src/Widgets/FadeBin.vala:170-211`), thread lines drawn from the avatar position (`src/Widgets/Status.vala:1712-1740`), media blur (`src/Widgets/FocusPicture.vala:120-137`). Icons are private symbolic assets named `tuba-*`.

### 7. Accessibility, keyboard, i18n specifics

Accessibility is explicit: `update_property` calls (`src/Widgets/MarkupView.vala:89-90`), roles set in `static construct` (`src/Widgets/StatusActionButton.vala:106`), and XML relations `described-by`/`labelled-by` (`data/ui/widgets/status.ui:88-90`, `data/ui/views/profile_header.ui:75-78`). Posts generate a spoken label governed by a 0–5 verbosity setting (`data/dev.geopjr.Tuba.gschema.xml:285-291`, `src/Widgets/Status.vala:1036-1100`). Keyboard: accelerators (`src/Application.vala:536-546`), `<Alt>1..9` tab switching (`src/Views/TabbedBase.vala:42-48`), XML-built `AdwShortcutsDialog` (`data/gtk/shortcuts-dialog.ui:3`). i18n: `Intl.bindtextdomain` (`src/Application.vala:271-273`), `translatable="yes"`, `ngettext` for counts (`src/Views/Sidebar.vala:28,40`), RTL-driven `xalign` (`src/Widgets/RichLabel.vala:13-20`).

### 8. Distinctive idioms worth copying

Inline custom emoji in labels via a widget-host label ported from Fractal:

```vala
// LabelWithWidgets is ported from Fractal
// Set `text` ... but use the `placeholder` keyword where widgets should be placed.
```
(`src/Widgets/LabelWithWidgets.vala:1-9`; consumer `src/Widgets/EmojiLabel.vala:20-31`)

Breakpoint-driven custom properties instead of CSS media queries:

```vala
var condition = new Adw.BreakpointCondition.length (
    Adw.BreakpointConditionLengthType.MAX_WIDTH, 670, Adw.LengthUnit.PX);
breakpoint.add_setter (this, "small", true);
```
(`src/Views/Base.vala:132-137`)

Also reusable: focus recovery after model edits (`src/Views/Timeline.vala:539-578`), `to_widget()` entities, header-as-first-item (`src/Views/Profile.vala:316-335`).

### 9. Anti-patterns / legacy leftovers / version-gated compromises observed

- A dead second list implementation: `USE_LISTVIEW` is commented out in `meson.build:12`, yet 37 `USE_LISTVIEW` lines in 12 files still fork `ContentBase`, `Timeline`, `Status` and others. The `ListView` path changes the `Status` parent class, but `data/ui/widgets/status.ui` hard-codes `GtkListBoxRow` (`src/Widgets/Status.vala:2-5` vs `status.ui:4-5`), so enabling the define would not build as-is.
- `Process.exit (0)` on an unexpected model item type (`src/Views/ContentBase.vala:139-142`) turns a data bug into an app kill.
- Framework-compensation hacks: 500 ms delayed focus grab "fighting with NavigationView over focus" (`src/Views/ContentBase.vala:120-122`), double-toggling `show_start_title_buttons` for back buttons (libadwaita #775, `src/Views/Base.vala:169-173`), manual `unbind_listboxes` for Vala ref cycles (`src/Views/Base.vala:145-156`).
- One widget per row, no recycling: insert batching (`src/Views/Timeline.vala:437-448,474-482`) and a 250-row cap for timestamp refresh (`src/Views/Timeline.vala:231-236`) mitigate, but exist because of the ListBox choice.
- Presentation state leaks into API objects via `tuba_row` (`src/Views/Accounts.vala:10-17`).
- Toast overlays are duplicated per dialog/view instead of one window-level service (`data/ui/dialogs/composer.ui:13`, `src/Views/Search.vala:419`).

### 10. Transferable lessons for a GTK4/libadwaita app

1. Decide early between `ListBox` (widget-per-row) and `ListView` + factories; Tuba's disabled `ListView` path has bit-rotted. Tuba ships `ListBox` in production with batching and a 250-row cap on timestamp refresh; the code does not say why the migration stopped.
2. Keep entities as model items through `to_widget()` so timelines, profiles and threads share one pipeline.
3. Express adaptation as `Adw.Breakpoint`-set named properties (`small`, `is-narrow`, `is-mobile`) consumed in code, not scattered `@media` rules.
4. Put empty/loading/error in one `Gtk.Stack` inside the base view; reserve `Adw.StatusPage` for standalone flows.
5. Use XML for static menus and dropdown factories, code for status-dependent menus, and per-view `SimpleActionGroup`s swapped in `on_shown`/`on_hidden` (`src/Views/Base.vala:208-216`). Give post widgets real accessible labels and relations.

## Bottles (bottles) — Python (PyGObject) + Blueprint

### 1. Stack and version baseline

Python 3 + PyGObject, pinned in `requirements.txt:15` (`PyGObject==3.56.3`); UI is Blueprint-only — 78 `.blp` files, zero hand-written `.ui`. Project version 67.4, `meson_version: '>= 1.5.0'` (`meson.build:3-5`), commit `cfd9665`.

No GTK/libadwaita minimum is declared anywhere; meson pins only itself. The floor is the Flatpak runtime: `org.gnome.Platform` runtime-version 50, base `org.winehq.Wine` `stable-25.08` (`build-aux/com.usebottles.bottles.Devel.json:4-7`). Nothing is version-gated (no `hasattr`, no `Adw.MAJOR` checks); imports assert `Gtk 4.0`/`Adw 1`/`GtkSource 5` (`bottles/frontend/main.py:36-40`). Usage implies libadwaita ≥ 1.7 (`Adw.WrapBox`, `ui/bottle-row.blp:38`), on top of 1.5-era `Adw.Dialog`/`Adw.PreferencesDialog` (`ui/preferences.blp:4`).

### 2. UI architecture and file layout

Layouts live in `bottles/frontend/ui/` (78 `.blp` + 2 CSS + `bottles.gresource.xml`): `blueprint-compiler batch-compile` writes `.ui` files into the build dir (`ui/meson.build:3,84`), which `gnome.compile_resources` bundles (`ui/meson.build:86`; entries in `ui/bottles.gresource.xml:4-80`).

Python splits by role: `views/` = pages in the main window, `windows/` = modal dialogs (even `Adw.Dialog` subclasses, so the split leaks), `widgets/` = rows. 76 classes carry `@Gtk.Template(resource_path="/com/usebottles/bottles/<file>.ui")` plus an explicit `__gtype_name__` (`windows/window.py:63-64`). Blueprint holds layout and strings; Python holds *all* behaviour — only one blueprint signal handler exists (`ui/window.blp:6` `close-request => $on_close_request()`, matched by `@Gtk.Template.Callback()` at `window.py:247`); everything else is a Python `connect`.

Resources are registered manually via a private API: `Gio.Resource.load(...)` then `._register()` (`bottles.py:48-52`).

### 3. Window, navigation and adaptive layout

One real toplevel: `Adw.ApplicationWindow` → `Adw.ToastOverlay` → `Adw.Leaflet main_leaf` → `Box[HeaderBar + Adw.Banner + SearchBar + Adw.ViewStack + Adw.ViewSwitcherBar]` (`ui/window.blp:4-80`). Leaflet pages are appended from Python at startup (`window.py:414-415`) and marked `set_navigatable(False)` (`window.py:417-418`); the stack gets `page_list`/`page_library` via `add_titled` with icons (`window.py:420-425`). Navigation is `self.main_leaf.navigate(direction=Adw.NavigationDirection.BACK)` (`window.py:497-498`).

Adaptivity is two nested `Adw.Leaflet`s (`ui/window.blp:9`, `ui/details.blp:5`) with `can-unfold: false`, plus headerbar button swapping on `notify::folded` (`details.py:102,115-119`). There are **no** `Adw.Breakpoint`s, `NavigationSplitView` or `OverlaySplitView` (0 hits). The library narrows by recomputing `max-children-per-line: bind template.items_per_line` (`ui/library.blp:25`, `views/library.py:129-141`).

The startup view persists by binding the setting onto the stack: `self.settings.bind("startup-view", self.stack_main, "visible-child-name", ...)` (`window.py:444-449`).

### 4. Lists, models, rows and item widgets

Bottles uses *no* `Gtk.ListView`/`GridView`/`ColumnView` (0 hits). Lists are `Gtk.ListBox` styled `boxed-list` inside an `Adw.PreferencesGroup` (`ui/list.blp:23-31`), filled by appending template rows that subclass `Adw.ActionRow` (`views/list.py:63-66`, `operation.py:27`). The library is a `FlowBox` with `set_filter_func` for search (`ui/library.blp:24`, `views/library.py:56`). Refreshes rebuild whole lists (`views/library.py:63-65`, `details.py:258-260`).

Legacy widgets survive in two places: `Gtk.ListStore` + `Gtk.TreeView` + `Gtk.CellRendererText` for processes (`views/bottle_taskmanager.py:54-70`, `ui/details-taskmanager.blp:4-8`) and the journal (`windows/journal.py:46,67-90`), including an `[internal-child selection] TreeSelection {}` declared in blueprint (`ui/dialog-journal.blp:75-76`). One `SignalListItemFactory` helper exists, only for `Adw.ComboRow` models (`utils/gtk.py:80-91`).

### 5. Dialogs, preferences, menus, toasts, banners, status pages

47 dialog classes: 25 `Adw.Window`, 17 `Adw.Dialog`, 4 `Adw.PreferencesDialog`, 1 `Adw.PreferencesWindow` (`windows/dlloverrides.py:85`, deprecated base) — versus 2 `Adw.ApplicationWindow`s (`windows/window.py:63`, `windows/bottlepicker.py:35`, CLI-only).

`Adw.Dialog` templates wrap `child: Adw.ToolbarView` + `Adw.HeaderBar` and open via `.present(parent)` (`ui/dialog-umu-search.blp:9-23`; `window.py:520,527-536`). Older `Adw.Window`s use `modal: true`, `set_transient_for()` in `__init__`, headerbar Cancel/Save and `self.destroy()` (`ui/dialog-rename.blp:4-22`, `windows/rename.py:38-40,56-60`).

Menus/popovers are *top-level objects outside* the template, referenced by id: `popover: pop_add` (`ui/window.blp:26`), `menu-model: primary_menu` (`ui/window.blp:38`), defined at `ui/window.blp:85-139`; bodies use `styles ["menu"]` + `$GtkModelButton` (`ui/program-entry.blp:5,44-58`).

Toasts: one `Adw.ToastOverlay` at the window root (`ui/window.blp:8`) with a single gateway `BottlesWindow.show_toast()` (`window.py:750-774`); 112 call sites use it. Banners: offline (`ui/window.blp:64-68`) and component-update (`ui/list.blp:17-20`, `views/list.py:375-396`). `Adw.StatusPage` is the universal empty/loading/offline state, even inside a `Stack` in a `PreferencesGroup` (`ui/preferences.blp:335-368`). Startup prompts use `Adw.AlertDialog` with markup body and string response ids (`window.py:157-172`).

### 6. Styling and resources

CSS is bundled and auto-loaded, not manual: `style.css` (231 lines) and `style-dark.css` (28) sit at the resource prefix (`ui/bottles.gresource.xml:4-5`), and `main.py:96` passes `resource_base_path="/com/usebottles/bottles"` to `Adw.Application`. `AdwApplication` therefore loads `style.css` unconditionally and `style-dark.css` in dark mode (the split-stylesheet scheme is deprecated since libadwaita 1.9; a `@media (prefers-color-scheme: dark)` block is the replacement). No code calls `CssProvider.load_from_resource` for either file. Three files declare `style_provider = Gtk.CssProvider()` as an unused *class attribute* (`views/library.py:46`, `views/bottle_details.py:122`, `windows/installer.py:122`). Styling combines these sheets with libadwaita theme classes and Python-added classes: `.donate`/`.supporter` (`window.py:142,233`), `.tag`/`.caption` chips (`windows/umu.py:183-184`), `grade-*` (`widgets/installer.py:69`).

Dark mode is a settings-driven `Adw.StyleManager.set_color_scheme(FORCE_DARK)` (`window.py:146-148`, `views/preferences.py:398-402`), though its row is `visible: false` (`ui/preferences.blp:17-27`). Icons: symbolic SVGs in `data/icons`; per-runner images via `set_from_resource(f"/com/usebottles/bottles/icons/scalable/apps/{icon}.svg")` (`views/bottle_details.py:126-129`). Custom drawing (playtime charts) is Cairo on `Gtk.DrawingArea`, reading `style_context.lookup_color("foreground")` and re-rendering on `notify::dark`/`notify::accent-color` (`widgets/playtimechart_weekly.py:66-67,218-222`).

### 7. Accessibility, keyboard, i18n

Accessibility metadata is almost absent: two `accessibility { label }` blocks in 78 blueprints (`ui/preferences.blp:553`, `ui/umu-prefix-row.blp:36`), no relations or alt text; `tooltip-text` is the de-facto label for icon-only buttons. Accelerators are registered in code through a helper (`main.py:107-113`, `main.py:498-512`).

Shortcuts window is unreachable: `ui/help-overlay.blp:3` defines a `ShortcutsWindow` (not a `$Template`), bundled as `gtk/help-overlay.ui` (`ui/bottles.gresource.xml:5`) and referenced by `action: "win.show-help-overlay"` (`ui/window.blp:128`), but no such action and no instantiating class exist.

i18n: `gettext.install()` before any GTK import (`bottles.py:46`) plus `bindtextdomain`/`textdomain` (`main.py:76-79`); `_()` and `C_()` in blueprints (`ui/help-overlay.blp:11`); `po/POTFILES` lists `.blp` files. UI language is switched via the `LANGUAGE` env var before GTK starts, hence "Quit and reopen" (`utils/localization.py:66-68`; `ui/preferences.blp:29-30`); font scaling pokes `gtk-xft-dpi` (`utils/gtk.py:30-77`).

### 8. Distinctive idioms worth copying

Sibling top-level popovers resolve by builder id while staying out of the widget tree. Children are grouped in `# region Widgets` blocks — 799 `Gtk.Template.Child()` declarations, all resolving to a real name in the matching `.blp` (verified programmatically).

Composite rows expose properties with a delegating setter:

```python
group = GObject.Property(
    type=Adw.ActionRow,
    default=None,
    setter=lambda self, group: self.check_button.set_group(group.check_button),
)
```
(`views/new_bottle_dialog.py:49-54`)

Thread boundaries are centralised, not per call site: `RunAsync` dispatches callbacks via `GLib.idle_add` when off-main-thread (`backend/utils/threading.py:101-107`), and handlers opt in with a decorator that rewrites kwargs for `idle_add` (`utils/gtk.py:151-163`, used at `operation.py:78`). Per-page search syncs with one toolbar button via `notify::search-mode-enabled` + `set_key_capture_widget(self)` (`window.py:427-431,784-806`).

### 9. Anti-patterns / legacy leftovers / version-gated compromises

- `$GtkModelButton` in 49 places (e.g. `ui/bottle-row.blp:16-28`) — not public GTK4 API; the `$` escape asks the builder for the raw GType, so this rides a private widget.
- `Gtk.MessageDialog` subclass reaching into private `self.message_area` (`windows/generic.py:23-45`) — deprecated widget as error dialog.
- `get_style_context()` 19×: 5 for class toggling (`widgets/installer.py:69`, `window.py:452,734`, `views/preferences.py:1144`) and 14 for `lookup_color` in the playtime charts — deprecated since GTK 4.10.
- `Adw.Leaflet` + `leaflet.navigate()` (`window.py:498`, `details.py:244-248`) is itself deprecated in libadwaita (replaced by `NavigationView`/split views + `Adw.Breakpoint`) and costs hand-written folded-state button swapping (`details.py:115-119`).
- `GObject.threads_init()` at module scope (`main.py:515`) — obsolete since PyGObject 3.11.
- Mutable class-level state: `__pages = {}` (`details.py:46`), `_TASK_WIDGETS` (`operation.py:64`), `settings` (`window.py:89`, `bottlepicker.py:39`), `style_provider`.
- Widget construction on a worker thread: `RunAsync(self.build_pages)` (`details.py:105`) mutates dicts there, then hops via `GLib.idle_add(ui_update)` (`details.py:200`).
- Dead assets: the shortcuts window, unused `searchbar` (`ui/window.blp:70`, 0 refs), orphan `ui/dialog-run-args.blp` (absent from `ui/meson.build` — 77 entries for 78 files — and the gresource), three unused class-level `CssProvider()` attributes, and a `style-dark.css` split that libadwaita 1.9 deprecates.
- Triple bookkeeping: `ui/meson.build` file list, `.ui` names in `bottles.gresource.xml`, `install_data` lists per dir (`windows/meson.build:4-45`).

### 10. Transferable lessons for a GTK4/libadwaita app

Blueprint can carry a 9,493-line UI over 78 files only if the Python side is disciplined: one template class per file, `__gtype_name__` always set, child names identical to blueprint ids. Centralise the main-loop hop and the toast overlay; otherwise both leak into every call site. Prefer `Adw.ToolbarView`/`Adw.Dialog` and `Adw.NavigationSplitView` over `Leaflet`. Keep one `style.css` at the resource base path and express dark/high-contrast variants with media queries instead of the deprecated `style-dark.css` split; delete unused `CssProvider()` attributes so the code does not imply a loading path it never takes.

## Workbench (workbench) — GJS (JavaScript) + Blueprint (.blp)

### 1. Stack and version baseline
Commit `f5832a0e651adba409f442ba5179a0fa7d7373cb`, version 51.0 (`meson.build:4`). GJS ES modules for the app (the root `meson.build` also enables Vala, C and Rust for demo and CLI tooling); no transpile step. Declared floors: GTK ≥ 4.10.0, GtkSourceView ≥ 5.8.0, libadwaita ≥ 1.5 (`src/libworkbench/meson.build:47-51`). These checks cover only the C helper library; the app declares nothing beyond `find_program('gjs')` (`src/meson.build:2`). The Flatpak runtime is `org.gnome.Sdk` 51 (`build-aux/re.sonny.Workbench.json:4-5`), so effective libadwaita is newer than the build floor. No `Adw.check_version`, `MINOR_VERSION` or `since` guard exists in `src/` (grep); gating is build-time only, on demos (§9).

126 `.blp` files: 10 in `src/`, 116 demo UIs under `demos/src/<Demo>/main.blp`. Zero `.ui` files are checked in.

### 2. UI architecture and file layout
Bundling uses `gjspack` (vendored `troll` submodule, absent here) in one custom target with `--prefix /re/sonny/Workbench` (`src/meson.build:53-70`). Blueprint is imported as a URI (`import resource from "./window.blp"`) and converted while packing, then loaded with `Gtk.Builder.new_from_resource` (`src/window.js:51,62`).

The main window is deliberately *not* a GObject template: one `.blp` holds an `Adw.ApplicationWindow`, three `menu` blocks, an `Adw.AlertDialog`, an experimental `Adw.Dialog` and a standalone `Button` as sibling roots (`src/window.blp:5,557,586,730,739,801,810`), all fetched by id. Reusable widgets are template classes with `Template` and `InternalChildren` (`src/widgets/CodeView.js:16,285-303`; `src/widgets/CodeFind.blp:3`), instantiated from Blueprint via `$Class` (`src/window.blp:198,262`). The C library has its own blueprint-compiler + gresource pipeline (`src/libworkbench/meson.build:118-131`), and its `.blp` is also imported into the JS bundle (`src/main.js:9`; `src/libworkbench/workbench-preview-window.c:37`). Secondary surfaces use `build(resource)` (call sites `src/Library/Library.js:27`, `src/Permissions/Permissions.js:30`, `src/shortcutsWindow.js:15`; the `build` helper lives in the unpopulated `troll` submodule).

`GObject.registerClass` is monkey-patched at startup to append a counter to every `GTypeName`, because GTypes cannot be unregistered while demo code registers classes repeatedly (`src/overrides.js:32-49`).

### 3. Window, navigation and adaptive layout
No `Adw.Breakpoint`, `Adw.NavigationSplitView` or `Adw.OverlaySplitView` is used by the app itself (demo-only). The shell is a fixed desktop layout: 1920×1200 default (`src/window.blp:5-7`), a vertical `Paned` holding a horizontal row of four `Adw.ToolbarView` panels — code, style, UI, preview — plus a bottom devtools `Adw.ToolbarView` (`src/window.blp:9,140,153,237,267,337,477`). Each panel's 46 px header is a `CenterBox` with `toolbar`/`panel_header` classes (`src/window.blp:157-174`). Visibility is state, not navigation: `ToggleButton` ↔ GSettings plus `bind_property("active" → "visible")` (`src/window.js:198-209`; `src/PanelStyle.js:7-18`). A placeholder icon shows when all panels are hidden (`src/window.js:211-218`). The preview swaps an inline `Viewport` for "windowed preview" status pages in a `Gtk.Stack` (`src/window.blp:389-460`). Devtools collapse hand-computes `Paned.position` from allocations (`src/Devtools.js:34-58`).

### 4. Lists, models, rows and item widgets
The demo browser is a `ListBox` with `boxed-list`, one `Adw.ActionRow` subclass per demo (`src/Library/Library.blp:78-84`; `src/Library/EntryRow.blp:4-40`). `EntryRow` exposes `demo` as `ParamSpec.jsobject` and a `triggered` signal carrying the chosen language (`src/Library/EntryRow.js:11-33,59-80`). Filtering mutates `widget.visible` in a JS loop and toggles an empty-state box (`src/Library/Library.js:118-146`). Drop-downs use `Gtk.StringList` filled imperatively (`src/window.js:78-84`; `src/Library/Library.js:67-90`), with selection bound to settings (`src/PanelCode.js:36-41`). The app has no `Gtk.ListView`; demos teach it (`demos/src/List View/main.blp:40`; `demos/src/Grid View/main.js:15`).

### 5. Dialogs, preferences, menus, toasts, banners, status pages
Permissions and Extensions are `Adw.Dialog` with `content-width/height`, `Adw.ToolbarView` + `Adw.HeaderBar` + `Adw.Clamp` (`src/Permissions/Permissions.blp:4-24`; `src/Extensions/Extensions.blp:4-30`), presented via `dialog.present(window)` (`src/Permissions/Permissions.js:46`, `src/Extensions/Extensions.js:52`). The save prompt is an `Adw.AlertDialog` with declarative `responses` and an `extra-child` of boxed rows (`src/window.blp:739-799`), save enabled imperatively (`src/window.js:526-533`); About uses a runtime `Adw.AboutDialog` (`src/about.js:28`). Menus are Blueprint `menu` blocks referenced by `menu-model` (`src/window.blp:586,730`); a theme selector is injected into the menu button's popover at runtime (`src/window.js:85-89`). No preferences window; color scheme is one mapped action (`src/actions.js:66`). Toasts exist only in demos (`demos/src/Toasts/main.blp:3`; `demos/src/Toasts/main.js:9-30`); the app's `Adw.ToastOverlay` is explicitly unused (`src/window.blp:138-139`). Status pages carry empty/unsupported-preview states (`src/window.blp:415,440,567`).

### 6. Styling and resources
`src/style.css` is imported as a module (`src/main.js:8`). It defines `panel_header`, `view-toggler`, `command_snippet` (using `var(--view-fg-color)`, `var(--border-color)`, `src/style.css:15-21`), a Builder-derived `button.pill.small` (`src/style.css:29-34`), and hides the dev banner through revealer rules (`src/style.css:43-51`). Icons are bundled by extension: `import "...svg" with { type: "icon" }` (`src/application.js:19-37`) plus a trimmed `Gtk.IconTheme` `resource_path` (`src/application.js:39-46`). GtkSourceView's scheme follows dark mode by looking up `Adwaita`/`Adwaita-dark` (`src/widgets/CodeView.js:200-205`). Demo CSS is scoped by prefixing selectors with `#workbench_output-<session id>` through postcss (`src/Previewer/Internal.js:23-24,255-271`), letting demo CSS stay naive (`demos/src/Styling with CSS/main.css:1-6`).

### 7. Accessibility, keyboard, i18n
Blueprint `accessibility { label: ... }` and `labelled-by`/`described-by` relations are used (`src/window.blp:381-383`; `src/Library/EntryRow.blp:7-9`); the Accessibility demo shows Orca shortcuts (`demos/src/Accessibility/main.blp:20-24`). Mnemonics come from `_("_Code")` + `use-underline` (`src/window.blp:41-42`). Accelerators are attached in JS via `application.set_accels_for_action` (`src/window.js:380-411`) and documented in a `Gtk.ShortcutsWindow` (`src/shortcutsWindow.blp:3`). i18n is gettext: `_()` in Blueprint, `import { gettext as _ } from "gettext"` in JS (`src/Library/Library.js:17`), `pkg.initGettext()` (`src/main.js:5`); no `po/` exists in this branch — translations come from a separate `l10n` branch (`docs/maintenance.md:10-15`).

### 8. Distinctive idioms worth copying
Blueprint `bind` expressions linking sibling widgets' properties declaratively (`demos/src/Overlay Split View/main.blp:24-39`) and breakpoint `setters` naming sibling objects (`demos/src/View Switcher/main.blp:9-14`).

Template demos have no root: the previewer extracts `<template>`, suffixes its class name to dodge GType collisions, instantiates the declared parent as a synthetic root, and gives demo code the raw template (`src/Previewer/Previewer.js:333-361`; `demos/src/Custom Widget/main.blp:3` consumed as `Template: workbench.template` in `demos/src/Custom Widget/main.js:4-14`).

Blueprint compiles through the language server, not a subprocess: `blueprint-compiler lsp` (`src/common.js:24`) and a custom `textDocument/x-blueprint-compile` request (`src/langs/blueprint/BlueprintDocument.js:16-27`); the XML pane is a projection of that output (`src/PanelUI.js:59-72`).

Demo contract: `main.json` (category, description, panels, autorun) plus language files detected by filename (`build-aux/library.js:64-85`); the index is generated at install time (`build-aux/library.js:98-113`).

### 9. Anti-patterns / legacy leftovers / version-gated compromises
- Hardcoded `#fcfcfc`/`#262626` and a hand-written `@media (prefers-color-scheme: dark)` block instead of Adwaita named colors (`src/style.css:39-65`).
- Filtering by hiding rows rather than a filter model (`src/Library/Library.js:138`).
- An `Adw.ToastOverlay` wraps the whole `Paned` content although no toast is shown; the comment says "Not used currently but we keep it in case we need it" (`src/window.blp:138`).
- Duplicated logic, e.g. a FIXME to merge blueprint diagnostics code with the CLI copy (`src/langs/blueprint/blueprint.js:56`).
- `Gtk.ShortcutsWindow` is used although deprecated in 4.18, against a 4.10 floor; the Vala CLI carries an allowlist of deprecation warnings (`src/cli/vala.js:31-53`).
- Declared libadwaita 1.5 but demos use `Adw.Spinner` and 1.6 doc links (`demos/src/Spinner/main.blp:13,34`), gated only by `"runtime-version": "47"` (`demos/src/Spinner/main.json:6`) checked at build time against the Flatpak runtime (`build-aux/library.js:236-249`); no runtime guard exists. [INFERENCE: `Adw.Spinner` postdates 1.5; the repo states no introduction version.]

### 10. Transferable lessons for a GTK4/libadwaita app
- One `.blp` per reusable widget (`Template` + `InternalChildren`), one builder-based `.blp` per multi-root surface. Workbench itself patches `GObject.registerClass` to append a counter to every GType name (`src/overrides.js:32-49`), because demo code re-registers classes.
- Put panel visibility in GSettings and bind it to header-bar toggles; do not build navigation infrastructure for a desktop tool with side-by-side panels.
- Workbench co-locates `menu`, `Adw.AlertDialog` and `Adw.Dialog` roots with its window in one Blueprint file and fetches them by id; it uses separate files for Permissions, Extensions, Library and shortcuts.
- Use `Adw.Clamp` + `boxed-list` + templated rows for browser lists; a `visible`-flag filter is tolerable only at this scale (≈116 rows).
- Express "cannot render inline" with `Adw.StatusPage` inside a `Gtk.Stack`, not an error label.
- If you ship sample UI, generate metadata into one index and keep per-language snippets as sibling files; the `workbench` global (`src/Previewer/Internal.js:76-94`) is the seam separating app UI from bundled sample code.

## Cross-project overview

### The shared skeleton

Eight independent codebases converge on the same shell:

```
Adw.ApplicationWindow
└── Adw.ToastOverlay            (usually)
    └── Adw.ToolbarView
        ├── [top] Adw.HeaderBar
        └── content
```

`Adw.ToolbarView` appears in every project, from 2 files in Amberol to 45 in
Fractal. Every main window is an `Adw.ApplicationWindow`. Toasts sit at the
window root in Nautilus, Fractal, Amberol, Shortwave, Tuba and Bottles; Text
Editor has no toasts at all, and Workbench ships an overlay it never calls.
Menus are declared as `GMenuModel` resources in the UI file, actions are
`win.*`/`app.*` GActions, and settings are GSettings properties.

The resource-path convention is the most quietly shared decision. Every app
with a custom stylesheet relies on `AdwApplication` auto-loading `style.css`
from the resource base path; none installs a provider for its main sheet.
Nautilus, Text Editor, Fractal, Amberol, Shortwave and Workbench set the base
path explicitly at construction. Tuba relies on the default derived from the
application ID (`dev.geopjr.Tuba` → `/dev/geopjr/Tuba`). Bottles sets it to
`/com/usebottles/bottles` (`bottles/frontend/main.py:96`). This is why a
stylesheet can look "dead" when the base path is missing or set after
construction — the file is bundled but never loaded.

### Navigation and adaptation: four shapes

There is no single navigation answer. The projects choose by what the sidebar
*is*, not by which widget is newest:

| Shape | Projects | What it buys |
|---|---|---|
| `Adw.OverlaySplitView` | Nautilus, Text Editor (wide), Amberol, Shortwave (wide), Tuba | A sidebar that can be revealed over content, with the content staying alive while the sidebar is hidden |
| `Adw.NavigationSplitView` | Fractal (room list/chat); Tuba (admin dashboard only) | Sidebar and content as navigation pages, with collapsed mode handling the drill-down |
| `Adw.MultiLayoutView` | Text Editor, Shortwave | Wide and narrow need different chrome: `OverlaySplitView` on the left, `BottomSheet` on the right |
| Fixed panels or `Adw.Leaflet` | Workbench (four fixed panels); Bottles (`Leaflet`) | A desktop tool that does not adapt, or a pre-1.6 layout kept alive |

Fractal is the clearest `NavigationSplitView` case: one window boolean
(`template.compact`) is bound into `split_view.collapsed`
(`fractal/src/session_view/mod.blp:15-16`), and selection calls
`set_show_content` (`mod.rs:211`). The sidebar and content are both
`Adw.NavigationPage`s, so the split view needs no manual page switching.
Tuba uses `OverlaySplitView` plus a separate `Adw.NavigationView` for the
content (`tuba/data/ui/dialogs/main.ui:22-29`); the admin dashboard is the one
place where it needs the coupled split-view model.

The adaptive idiom is consistent: declare a breakpoint in the UI, set a small
number of properties, and let code react to the derived state. Breakpoints
cluster between 550sp and 700sp:

| Project | Breakpoint(s) | What the setters change |
|---|---|---|
| Nautilus | 682sp | collapse split view, show sidebar button, hide history/view controls and tab bar, reveal action bar |
| Text Editor | 650sp, 400sp | switch `MultiLayoutView` layout; at 400sp also swap the Open button's icon stack |
| Amberol | 560sp | collapse split view; code shows the playlist back button |
| Fractal | 600sp | set `compact`; bound into `NavigationSplitView.collapsed` |
| Tuba | 700sp, 670px, 550sp, 430sp | collapse split view, set `is-mobile`, set `small`, set composer `is-narrow` |
| Shortwave | 625px width, 165px height | switch `MultiLayoutView` layout; switch to the mini-player stack |

Bottles and Workbench are the exceptions. Bottles uses two `Adw.Leaflet`s and
swaps header buttons in `notify::folded` handlers (`bottles/frontend/views/
details.py:105-112`); Workbench is a fixed 1920×1200 desktop tool. The skill's
360px test remains the right floor, but the observed design breakpoints are
higher: the apps switch to narrow chrome around 600–700sp, with 400–550sp for
secondary changes.

`Adw.Sidebar` and `Adw.ViewSwitcherSidebar` (libadwaita 1.9) have **zero**
adoption in these eight main branches. Nautilus still carries a 4.2k-line
custom `Gtk.ListBox` sidebar (`nautilus/src/nautilus-sidebar.c`); Tuba builds
its sidebar from composed `GListModel`s and a `Gtk.ListBox`. The new API is too
young to be treated as the default.

### Lists: virtualization is the dividing line

Two regimes coexist, and the split is consistent:

| Need | Observed implementation |
|---|---|
| Long, homogeneous or heterogeneous data | `GtkListView`/`GtkGridView`/`GtkColumnView` + `GListModel` + factories |
| Settings, pickers, short static lists | `GtkListBox` + `boxed-list`, one templated row per item |
| Legacy | `GtkTreeView` + `GtkTreeStore`/`GtkListStore` (Bottles only) |

The virtualized camp is Nautilus, Text Editor, Fractal, Amberol and Shortwave.
Nautilus documents a full chain — `GListStore` → `GtkFilterListModel` →
`GtkTreeListModel` → `GtkSortListModel` → selection (`nautilus-view-model.c:
19-35`) — and switches between grid, column and list views. Fractal runs 12
`ListView`s and a `GridView`, uses two factory styles, and reuses a
message row's child widget on rebind when the event type is unchanged, swapping
it only when the type changes (`room_history/event_row.rs:289-300`). Amberol keeps the model
chain in code and binds row properties with `property_expression`
(`window.rs:1096-1149`). Shortwave uses a `GridView` with inline CDATA
`GtkBuilderListItemFactory` templates and a hand-written `GListModel`
(`library_page.ui:41-61`).

The boxed-list camp is equally deliberate: settings rows, language pickers,
and the Workbench demo browser. All eight use `GtkListBox` somewhere except
Amberol, which has no settings list (settings are menu toggles). Boxed lists
are not legacy; they are the right tool for short, static content.

Tuba is the cautionary case. Its timelines bind a `GListStore` of
`Widgetizable` objects to a `GtkListBox` (`ContentBase.vala:32-38`), one
widget per row, with batched inserts and a 250-row cap on timestamp refresh
(`Timeline.vala:231-236,437-482`). A `USE_LISTVIEW` branch is commented out in
`meson.build:12`, yet 37 `#if USE_LISTVIEW` lines in 12 files remain, and
`status.ui` hard-codes `GtkListBoxRow`, so the disabled path no longer matches
its own template. The code does not say why the migration stopped.
Bottles uses `ListBox`/`FlowBox` for everything and keeps `TreeView` for the
task manager and journal. For Babble's timelines, the Fractal/Amberol model is
the relevant one: virtualize, recycle, and keep filtering/sorting in models,
not in `visible` flags.

### Dialogs and feedback: migration in progress

`Adw.Dialog` and its subclasses have won on paper: all eight projects use
`Adw.Dialog`, six use `Adw.PreferencesDialog`, seven use `Adw.AlertDialog`
(Amberol has no alert flow; Workbench uses it for the save prompt), and six
use `Adw.ShortcutsDialog`. The exceptions show the migration debt:

- Bottles has 47 dialog classes: 25 `Adw.Window`, 17 `Adw.Dialog`,
  4 `Adw.PreferencesDialog` and 1 `Adw.PreferencesWindow`. Older windows set
  `modal: true` and `transient_for` by hand and call `destroy()`; newer
  templates wrap `Adw.ToolbarView` + `HeaderBar` and use `present(parent)`.
- Fractal keeps `Adw.PreferencesWindow` for room details and special-cases it
  in the toast walker (`utils/toast.rs:171-178`). The reason is documented
  inline; the compromise is deliberate.
- Text Editor uses three `GtkInfoBar`s for access errors, restored documents
  and encoding problems. Nautilus still uses a `GtkInfoBar` for its
  removable-media content bar (`nautilus-x-content-bar.c`). `Adw.Banner` is the modern replacement, and
  Nautilus, Fractal, Tuba and Bottles use banners for persistent state.
- Bottles still calls `Adw.MessageDialog` in several places and subclasses
  `Gtk.MessageDialog` in `windows/generic.py:23-45`; both are deprecated in
  favour of `Adw.AlertDialog`.

Toast ownership is the least standardized part. Bottles centralizes 112 call
sites through one `show_toast()` gateway on the window
(`windows/window.py:750-774`). Tuba emits an application-level `toast` signal
that the main window forwards to its overlay (`Dialogs/MainWindow.vala:39,
50-58`). Shortwave has a window-level overlay (`window.ui:24`) as the default sink
for `DisplayError` and adds its own overlay to three of its five dialogs
(station, track, device) for toasts raised from them; it also dedupes dialogs
by checking `visible_dialog()` (`window.rs:84-92`). Tuba's composer and
search views create local overlays too. The tradeoff is real: a
window-level service avoids missing overlays and duplicated plumbing; a
dialog-local overlay keeps feedback next to the action. Whichever you choose,
make it a rule and route all calls through it.

`Adw.Banner` is used for persistent, non-blocking states (offline, call room
verification, pending knocks, component updates). `Adw.StatusPage` is the
universal empty/error page, but Tuba deliberately does not use it for
timeline loading/empty states: `base.ui` has a `Gtk.Stack` with spinner and
message pages so the header and tab bar remain stable (`data/ui/views/
base.ui:46-140`). That is a useful distinction: status pages for standalone
flows, in-page stacks for timeline states.

### Styling: conventions, not themes

Every app with custom CSS relies on the resource base path and a single
`style.css` (Fractal compiles 1,202 lines of SCSS to it; Bottles adds a
28-line `style-dark.css`; Tuba's 1,347-line sheet excludes its easter-egg
CSS). No scanned stylesheet uses `@define-color`; the common vocabulary is
`var(--accent-bg-color)`, `var(--card-bg-color)`, `color-mix()`, and media
queries for dark and high contrast. Text Editor also generates a recoloring
provider at runtime for its source view, and Amberol injects a per-track
palette at provider priority 400; those are the exceptions that prove the
rule that the main sheet needs no loader.

The selector strategy splits by language culture:

- **Custom element names** via `set_css_name()` in Amberol, Fractal,
  Shortwave and Tuba. This keeps the CSS tree semantic (`queuerow`,
  `songcover`, `event-row`, `ttl-view`) and avoids class soup, but couples
  the app to its own CSS element names.
- **Style classes and widget names** in Nautilus and Text Editor, with
  structural selectors into GTK's list widgets (`gridview > child`,
  `columnview > listview > row > cell`). Those are documented GTK CSS nodes,
  but they couple the sheet to GTK's node layout and need matching CSS work
  whenever a pane moves between widget types.
- **Class strings from Python** in Bottles, plus hard-coded hex colors and the
  pre-1.9 `style-dark.css` split.

Two kinds of structural coupling appear, with different risk. Selectors into
**libadwaita internals** are the fragile kind; the only clear case is Amberol's
`.main-window > overlay-split-view > widget.background`, plus Fractal's vendored
copy of libadwaita 1.6.1 colour math in `_vendor.scss`. Selectors into
**documented GTK nodes** (Nautilus's `columnview > listview > row > cell`,
Fractal's `.room-history-list > row`) or into the app's own class tree (Tuba's
`.ttl-view:not(.large-view)>.fake-content>.card`) are coupling, not private
access. The rule — style your own widget by element name or class, never
through a libadwaita internal's node hierarchy — is broken by one project in
the eight.

`.dim-label` versus `.dimmed` is still unsettled. Nautilus, Fractal and
Shortwave have mostly moved to `.dimmed`; Tuba (33 uses) and Bottles (44 uses)
still rely on `.dim-label`, with Text Editor, Amberol and Workbench also
using it. The skill's `.dimmed` guidance is correct for new code, but a
codebase-wide migration is clearly not a precondition for shipping.

### Blueprint versus GtkBuilder XML

Blueprint is the majority format (five of eight) and handles the largest UIs:
Fractal (148 files), Bottles (78), Nautilus (33), Workbench (10 app files plus
116 demos) and Amberol (8). XML survives in Text Editor, Tuba and Shortwave,
plus Nautilus' context menus.

The observed strengths match the formats' reputations:

- Blueprint makes bindings, expressions, breakpoint setters and menus compact
  enough to keep in the template. Text Editor's wide/narrow layouts, Amberol's
  breakpoint and Shortwave's `AdwMultiLayoutView` would all be more verbose in
  XML. Batch compilation is standard (`blueprint-compiler batch-compile`), and
  Fractal works around meson's flat-output limitation with `alias=` entries to
  restore nested resource paths (`src/meson.build:8-46`).
- XML's `<binding>`/`<lookup>` chains are powerful: Shortwave has 32 binding
  blocks across nine files, Text Editor binds row subtitles and open-popover
  state declaratively, and Tuba uses XML callbacks with `swapped="no"`.
  Inline CDATA `GtkBuilderListItemFactory` templates (Nautilus, Text Editor,
  Shortwave) keep one-off rows out of code. XML needs no codegen, but it is
  verbose and easier to leave stale.

In all formats, the division is the same: UI files hold the tree, strings,
menus and simple bindings; code holds behaviour. C and Rust bind template
children to typed fields; Vala uses `[GtkChild]` and `[GtkCallback]`; Python
declares 799 `Gtk.Template.Child()` fields and wires almost everything with
`connect`; GJS uses `Gtk.Builder` + `GObject.registerClass`, and Workbench
deliberately keeps its main window as a multi-root builder file rather than a
GObject template.

### Versioning and bindings

None of the eight gates libadwaita at runtime, and every one relies on a hard
floor. Nautilus, Text Editor, Fractal, Amberol, Shortwave and Tuba declare
explicit meson minimums; Bottles and Workbench rely on their Flatpak runtime,
with Workbench's C helper declaring its own floors. Tuba is the only project
that gates GTK 4.20/4.22 APIs with generated Vala defines
(`meson.build:126-132`), and no project gates libadwaita at runtime.

That is a coherent strategy for Flatpak-first apps, and it differs from the
skill's advice to use `ADW_CHECK_VERSION`/`gtk_get_minor_version()` for newer
APIs. Runtime checks matter when the same binary must support older distro
runtimes; a bundled runtime can simply require the new floor. For Rust apps the
binding features are a second, separate floor. Amberol's `Cargo.toml` enables
GTK `v4_16` and libadwaita `v1_5` while meson requires 4.19.5/1.8.rc, and
Shortwave's libadwaita-rs features stop at 1.7 while meson requires
1.8.alpha. The audit found no Rust code in either that needs newer API: the
1.8 requirement comes from Blueprint/XML-only widgets such as
`AdwShortcutsDialog`, which need no crate feature. So the gap is latent, not a
defect: it only matters when Rust code first needs a newer symbol.

### Accessibility

Accessibility quality varies more than any other axis. Fractal is the
strongest: 81 `accessibility` blocks, 110 accessible roles, focus routing into
the visible stack page, and explicit relations. Tuba is close: real roles,
`described-by`/`labelled-by` relations, and a spoken post label whose
verbosity is a user setting. Nautilus sets roles in class init and binds
accessible names through `GtkExpression` chains, including for list cells.
Text Editor, Shortwave and Workbench use labels and roles selectively.
Bottles has two `accessibility` label blocks across 78 blueprints and otherwise relies on tooltips.

The recurring, copyable idiom is the same in both formats: declare
`accessibility { label, labelled-by, described-by }` next to the widget, set
roles that the builder cannot express in `class_init`, and give every
icon-only button a tooltip plus an accessible label. Custom rows (statuses,
file cells, message events) need a composed spoken label; Tuba's
`Status.vala:1036-1100` is the most complete example.

### Anti-patterns observed

These are observed in shipping code, not hypothetical. Most are migration debt
rather than ignorance, and the listed replacement is the one the project
itself is moving toward.

| Pattern | Seen in | Why it is a problem | Modern replacement |
|---|---|---|---|
| `Adw.Leaflet` + manual folded-state handlers | Bottles | Deprecated; navigation and header state are hand-synchronized | `OverlaySplitView`/`NavigationSplitView` + `Adw.Breakpoint` |
| `GtkInfoBar` for persistent state | Nautilus, Text Editor | Deprecated since GTK 4.10 | `Adw.Banner` (persistent) or `Adw.Toast` (transient) |
| `Gtk.MessageDialog` / `Adw.MessageDialog` | Bottles | Deprecated; private `message_area` access in the `Gtk` case | `Adw.AlertDialog` |
| `Gtk.TreeView` + `Gtk.ListStore` | Bottles | Deprecated, no virtualization path forward | `GtkListView`/`GtkColumnView` + `GListModel` |
| `$GtkModelButton` | Bottles (49 uses) | Private GTK widget, not public API | Public buttons / `Gtk.PopoverMenu` |
| `Adw.PreferencesWindow` | Fractal, Bottles | Deprecated | `Adw.PreferencesDialog` |
| Per-view `Adw.ToastOverlay` with no shared rule | Tuba (composer, search, admin) | Duplicated plumbing, easy to miss a toast | A window default plus a documented dialog exception, as Shortwave does |
| `Gtk.ListBox` for long timelines | Tuba | One widget per row, no recycling | `GtkListView` + factories + model |
| CSS selectors into libadwaita internals | Amberol (`widget.background`), Fractal (vendored libadwaita SCSS) | Break when libadwaita renames internal nodes | Style your own element/class |
| Dead UI resources and actions | Text Editor, Bottles, Shortwave | Misleads readers and translators; looks like working UI | Grep templates/resources in CI; delete or wire |
| `Process.exit(0)` on a model type mismatch | Tuba (`ContentBase.vala:139-142`) | A data bug kills the app | Skip/log the item |
| Unused `CssProvider()` attributes | Bottles | Implies a manual loading path that does not exist | Remove; rely on the resource base path |

## Comparison with the internal `gtk4-libadwaita-ui` skill

The skill's baseline (GNOME 51, GTK 4.24, libadwaita 1.10) matches the
newest projects in the corpus, and most of its non-negotiable rules are
confirmed by shipping code. The differences are about defaults, timing and
distribution model, not about which widgets are modern.

| Skill guidance | Observed practice | Verdict |
|---|---|---|
| `AdwApplicationWindow` + `AdwToolbarView`; never `set_child`/`set_titlebar` | Every main window is `Adw.ApplicationWindow`; `Adw.ToolbarView` appears in all eight | Confirmed |
| `AdwDialog`, `AdwAlertDialog`, `AdwPreferencesDialog`, `present(parent)`; never `GtkDialog`/`AdwMessageDialog`/`AdwPreferencesWindow` | `AdwDialog` in 8/8; `AlertDialog` in 7/8; `PreferencesDialog` in 6/8; `PreferencesWindow` in 2, `Gtk.MessageDialog` in 1 | Confirmed as direction; migration is incomplete in older code |
| `AdwNavigationSplitView`, `AdwOverlaySplitView`, `AdwBreakpoint`, `AdwMultiLayoutView`; never `AdwLeaflet`/`AdwFlap` | `OverlaySplitView` in 5, `NavigationSplitView` in 2, `MultiLayoutView` in 2, `Leaflet` in 1 | Confirmed; the skill should say *when* each is appropriate |
| `AdwViewStack` + `AdwViewSwitcher`/`Bar`; `AdwSidebar`/`ViewSwitcherSidebar` (1.9) | `ViewStack` in 3; `Sidebar`/`ViewSwitcherSidebar` in 0; custom sidebars in Nautilus/Tuba | New sidebar API is unproven; present it as an option, not a default |
| Breakpoints for adaptive layout; test at 360px | 6/8 use breakpoints; observed breakpoints 550–700sp; Bottles uses `Leaflet`, Workbench is fixed | Confirmed; add typical ranges and the 400/550 secondary breakpoints |
| `GtkListView`/`GridView`/`ColumnView` + models; boxed lists for short lists | Virtualized views in 5/8; `ListBox` in 7/8 for short lists; `TreeView` only in Bottles | Confirmed; add the timeline-versus-settings decision rule |
| `Adw.Toast`, `Adw.Banner`, `Adw.StatusPage`, `Adw.Spinner` | Toasts in 6 active apps; banners in 4; status pages in 8; spinners in 4; Text Editor still uses `GtkInfoBar` | Confirmed; add toast ownership guidance |
| `AdwShortcutsDialog` (1.8); automatic `app.shortcuts` | 6/8 use it; Workbench uses deprecated `Gtk.ShortcutsWindow`; Bottles has an unreachable shortcuts window | Confirmed; the auto-load convention is worth spelling out |
| CSS variables and media queries; never `@define-color` or split dark stylesheets | No scanned sheet uses `@define-color`; all use vars/`color-mix`; Bottles still uses `style-dark.css` | Confirmed; one legacy exception |
| `.dimmed`, never `.dim-label` | `.dim-label` remains in 5/8 projects, with 33–44 uses in Tuba/Bottles | Correct rule, incomplete adoption; say it is a migration target |
| Runtime version checks for newer APIs | 1/8 gates GTK; 0/8 gate libadwaita; the rest hard-floor through meson/Flatpak | Audience-dependent; clarify Flatpak-first versus distro-wide support |
| One `style.css`; no private-structure selectors | All main sheets auto-load; private selectors in 4/8; custom element names in 4/8 | Confirmed, with concrete counterexamples to cite |

### What the skill should add

1. **A navigation decision table.** Lead with the choice, not the widget name:
   persistent revealable sidebar → `OverlaySplitView`; coupled list/detail
   navigation → `NavigationSplitView`; wide and narrow need different chrome
   → `MultiLayoutView` + `BottomSheet`; desktop tool with fixed panes → do not
   add navigation infrastructure. Use Fractal, Nautilus, Text Editor and
   Shortwave as the four examples.
2. **The automatic-resource convention.** Document that `AdwApplication` loads
   `style.css` and `shortcuts-dialog.ui`, and `GtkApplication` loads
   `gtk/menus.ui`, from the resource base path, and that the path must be set
   at construction. This is the single highest-leverage detail in the corpus:
   it removes loader code when correct and silently disables CSS/shortcuts
   when wrong.
3. **Versioning by distribution model.** Split the advice: bundled-runtime
   apps can hard-floor in meson and CI; distro-wide apps need runtime checks.
   Add the gtk-rs/adw-rs rule: crate features must be at least the meson
   floor, or newer declarative widgets will build against a lower binding
   level and fail unpredictably.
4. **List selection guidance.** Add a two-row table: long/heterogeneous lists
   → `ListView`/`GridView`/`ColumnView` with models and factories; settings
   and short static lists → `ListBox` + `boxed-list`. Warn against leaving a
   disabled second path in the tree, as Tuba's `USE_LISTVIEW` does.
5. **Toast ownership.** Require one window-level overlay and a single gateway,
   with a documented exception for dialogs that need local feedback. This
   prevents the unplanned duplication seen in Tuba; Shortwave's window default
   plus dialog overlays is the documented-exception form.
6. **Accessibility for custom rows.** Add a worked example of a composed
   accessible label for a status/message row (`labelled-by`/`described-by`,
   role, spoken text), because the skill currently covers generic widget
   accessibility but not the hardest case in a social client.
7. **A "dead UI" check.** Add a CI grep for templates, shortcuts dialogs,
   menus and CSS that are bundled but never referenced. Text Editor,
   Bottles and Shortwave all ship at least one.
8. **Newer widgets with adoption data.** Mark `AdwSidebar` and
   `AdwViewSwitcherSidebar` as emerging (0/8 here), `AdwMultiLayoutView` and
   `AdwBottomSheet` as proven in adaptive apps (2/8 each), and keep
   `AdwSpinner` optional (4/8).
9. **Multi-root Blueprint.** Mention the Workbench pattern — one `.blp` with
   window, menus and dialogs as sibling roots, fetched by ID — as a valid
   alternative to one file per class, with the caveat that it loses
   template-child typing.

### Benchmark-derived recommendations

Babble is closest to Tuba and Fractal: a social timeline, a persistent
navigation shell, media attachments, and a Python/PyGObject codebase.

1. **Shell:** keep `Adw.ApplicationWindow` → `Adw.ToastOverlay` →
   `Adw.ToolbarView` → `Adw.HeaderBar`. Use `AdwOverlaySplitView` for the
   sidebar unless the app needs coupled list/detail navigation; if the narrow
   layout needs a bottom sheet, use `AdwMultiLayoutView` with
   `OverlaySplitView` and `BottomSheet`, not a second window structure.
2. **Breakpoints:** declare narrow mode at 600–700sp, plus secondary
   breakpoints around 400–550sp for controls. Set properties in the
   breakpoint and react in one notify path; do not hand-code folded-state
   button swapping.
3. **Timelines:** use `GtkListView` + `GListModel` + factories. Keep
   filtering and sorting in `GtkFilterListModel`/`GtkSortListModel`, bind row
   properties with expressions, and recycle heterogeneous rows by swapping a
   child rather than rebuilding the row. Reserve `GtkListBox`/`boxed-list`
   for settings and short pickers.
4. **Templates:** one Blueprint or `.ui` per widget, one class per file,
   child names equal to the template IDs, `__gtype_name__` always set in
   PyGObject. Keep all main-loop hops in one helper; never construct widgets
   on a worker thread; avoid mutable class-level state. Bottles' scale proves
   the discipline works; its class-level `__pages`/`settings`/`style_provider`
   fields show what happens without it.
5. **Dialogs and feedback:** standardize on `Adw.Dialog`,
   `Adw.AlertDialog`, `Adw.PreferencesDialog` and `Adw.ShortcutsDialog`; route
   every toast through one window-level gateway. Use `Adw.Banner` for
   persistent state (offline, rate limit, pending verification) and a
   `Gtk.Stack` for timeline loading/empty/error states, reserving
   `Adw.StatusPage` for standalone flows.
6. **CSS:** one `style.css` at the resource base path; style Babble's own
   widgets by element name or class; never select libadwaita private nodes.
   Use `var(--…)`/`color-mix()` and media queries, and prefer `.dimmed` in
   new code while migrating existing `.dim-label` uses.
7. **Accessibility:** give every icon-only button a tooltip and accessible
   label; for status rows, compose a spoken label and expose it through
   `labelled-by`/`described-by`. Tuba's verbosity setting is a good model for
   a social timeline.
8. **Versioning:** keep the GTK/libadwaita floors in one place and align any
   Python/binding runtime requirements with them. Babble ships a Flatpak, so
   hard floors plus CI are acceptable; add runtime checks only if the app must
   support older distro runtimes.

## Babble in context

Babble was not one of the eight; it was inspected afterward with the same greps and file reads (not cloned or re-ranked), so
the comparison is against its current `main` rather than against the
recommendations above. The short version: on the evidence of this survey,
Babble is **ahead of the corpus on widget modernity and UI rule enforcement,
behind it on UI-definition ergonomics**. Its shell, lists, dialogs and CSS
are already the patterns the best of the eight use; its dialogs and rows are
assembled in Python, and its toast overlays are repeated per dialog.

### Where Babble sits

| Dimension | Babble | The eight |
|---|---|---|
| Stack | Python 3 + PyGObject; GTK 4.24 / libadwaita 1.10 (`meson.build:3-4`) | GTK 4.16–4.24, libadwaita 1.5–1.8 |
| UI definition | 2 XML UI files (`window.ui`, 670 lines, the only template; `shortcuts-dialog.ui`, 255, an `AdwShortcutsDialog`) and 69 non-test Python modules | 8–148 declarative UI files; five of eight use Blueprint |
| Shell | `AdwApplicationWindow` → `GtkOverlay` (media) → `AdwToastOverlay` → `GtkStack` (login/shell) → two `AdwOverlaySplitView`s → `AdwNavigationView` | Same spine; `AdwToolbarView` in 8/8 |
| Sidebar | `AdwSidebar` for the full sidebar plus a custom 64px icon rail (`window.ui:375`, `sidebarrail.py`) | 0/8 use `AdwSidebar`; Nautilus and Tuba build custom sidebars |
| Adaptivity | Three breakpoints: 600sp closes the sidebar, 601sp reopens wide mode, 1100sp pins the web column (`window.ui:21-51`) | 550–700sp, usually a single `max-width` collapse |
| Lists | `GtkListView` + `SignalListItemFactory` over custom `RunModel`/`TimelineModel` (`timelinepage.py:215-222`); `GtkListBox` + `boxed-list` for secondary pages (`pages.py:587`) | Same two regimes: 5/8 use `ListView`/`GridView`; boxed lists for settings |
| Dialogs | Twelve `Adw.Dialog` subclasses, ten `Adw.AlertDialog` references in seven files, one `Adw.PreferencesDialog`; no legacy dialog widgets | Migration in progress; Bottles still has 25 `Adw.Window` dialogs |
| Toasts | Eleven production files own an `Adw.ToastOverlay`; `pages.show_toast()` walks ancestors and falls back to the window (`pages.py:398-425`) | Bottles: one window gateway. Shortwave: window default plus overlays on three dialogs. Tuba: per-view overlays |
| CSS | 914 lines, 102 `var(--…)` occurrences, 21 `color-mix()`, six `@media` blocks (five high-contrast, one reduced-motion), seven `.dimmed`, zero `@define-color` or `.dim-label` | Same vocabulary; `.dim-label` survives in 5/8 |
| Accessibility | `StatusRow` is an `ARTICLE`; 71 `update_property` calls, six relations, accessible labels on icon buttons | Strongest in Fractal/Tuba; almost absent in Bottles |
| Tests | 45 standalone test scripts (UI and behaviour), including CSS parsing, application action rules, UI wording, sidebar state, composer geometry and timeline memory | The scan did not surface equivalent UI rule tests |

The shell is almost exactly the corpus's shared skeleton, with one addition:
the media viewer overlays the window instead of being a dialog
(`window.ui:45-50`). The comment there gives the reason: an `AdwDialog` floats
inside the window and can never fill it, so a fullscreen attachment would keep
a margin on every side.

### What the benchmark validates

- **The window shell.** `AdwApplicationWindow` + `AdwToolbarView` +
  `AdwToastOverlay` is what all eight do, and Babble's login, home and
  notifications pages each use the same header/toolbar arrangement.
- **The main navigation shape.** `AdwOverlaySplitView` + `AdwNavigationView`
  is Tuba's exact structure: a revealable sidebar whose content pane hosts a
  navigation stack. Babble also uses `AdwNavigationSplitView` once, for the
  admin dashboard (`admin.py:1645`), which is the same narrow exception Tuba
  makes for its admin dashboard.
- **The timeline list.** `GtkListView` + a signal factory + a custom
  `GListModel`, with `AdwClampScrollable` for the column, is the
  Fractal/Amberol model. Babble's `RunModel` grouping is the same idea as
  Fractal's grouping model; its shell keeps a column of cached `StatusRow`
  cards rather than swapping one child per event, which is a different
  recycling strategy for the same problem.
- **Dialog migration.** Twelve `Adw.Dialog` subclasses and no
  `Gtk.MessageDialog`, `Adw.MessageDialog`, `Gtk.Dialog`, `Gtk.InfoBar`,
  `Gtk.TreeView`, `Adw.Leaflet` or `Adw.PreferencesWindow` puts Babble ahead
  of the older end of the corpus, not merely at parity.
- **CSS conventions.** Palette variables, `color-mix()`, contrast media
  queries and `.dimmed` are exactly what Nautilus, Fractal and Shortwave use.
  The stylesheet has no `@define-color` and no `.dim-label`; the only
  hard-coded colors are the documented lightbox black/white pair (`style.css:438-466`) and the Catch-Up image scrim and on-image text (`style.css:841-847`), both commented as content rather than theme.
- **Hard version floors.** Babble's GTK 4.24 / libadwaita 1.10 floors match
  the corpus's dominant strategy: no runtime libadwaita gates. Six projects
  declare explicit meson floors; Bottles and Workbench rely on their Flatpak
  runtime. There is no binding-feature floor to drift out of sync with meson;
  the runtime either has the API or fails at first use.

### Where Babble diverges

**1. UI definition is code-first.** Two `.ui` files for 69 non-test modules is
an outlier in this comparison. Bottles, the other Python app, has 78 Blueprint
files; Tuba has 40 XML templates; Text Editor has 18. Babble keeps only the
window shell and shortcuts dialog declarative and builds dialogs, rows,
preference pages and the icon rail in Python.

That is defensible for `StatusRow`, whose optional parts are built lazily
(`_ensure_source_line`, `_ensure_carousel`, …) and whose 787 lines carry both
the widget tree and the row behaviour, and for the sidebar rail, which is
dynamic. It is less obviously
right for the twelve dialogs, where the same toolbar/header/overlay scaffold
is repeated. The corpus has a direct precedent for fixing exactly that:
Fractal's abstract `ToastableDialog` owns the `Adw.ToolbarView` + header +
`Adw.ToastOverlay` and exposes a `child-content` property
(`src/components/dialogs/toastable.rs:18-25`). Babble already has a partial
equivalent: `ToastDialog` in `lists.py:127` owns the toolbar, header, toast
overlay and an `Adw.PreferencesPage`, and four dialogs use it (`ListEditor`,
`_MembershipDialog`, `AdvancedSearchDialog`, `MultiHashtagEditor`). A second,
near-identical base, `AdminDialogBase` (`admin.py:251`), serves five admin
dialogs, so nine concrete dialogs sit on two bases. Ten others build the
scaffold by hand, including `ReportDialog`
(`report.py:164-196`, a `NavigationView` inside the overlay) and
`ProfileEditDialog` (`profileedit.py:236-239`).

**2. Toasts are dialog-local, with a central dispatcher.** Babble does not use
one window-level gateway; it gives most dialogs their own overlay and routes
through `pages.show_toast`, which finds the nearest ancestor overlay and falls
back to the window. That is the same hybrid Shortwave uses (window default plus
dialog overlays), with the routing done by ancestor lookup rather than by an
explicit `handle_error_in` call. This is more robust than raw per-view overlays: a toast
raised inside a dialog lands in that dialog, not behind it. It is also more
code than Fractal's base class and less centralized than Bottles' single
`show_toast()` gateway. The dispatch is centralized; the overlay is not.

**3. The breakpoint model is compact-first.** The window starts collapsed and
breakpoints widen it (`min-width: 601sp`, `min-width: 1100sp`), with a
complementary `max-width: 600sp` breakpoint that closes the sidebar in the
same pass that collapses the split view. The corpus mostly does the reverse:
wide by default, one `max-width` breakpoint collapses. Babble's pair exists
to avoid painting the wide sidebar as an overlay for one frame, and the code
defers `show-sidebar` to an idle to avoid re-entering the split view's
allocation (`window.py:1318-1350`). It is more state than Nautilus or Amberol
carry, but it is deliberate and the pure state functions live in
`sidebarmode.py` with a matrix test.

**4. Two split views are nested.** The web-link column is an
`AdwOverlaySplitView` inside the main one (`window.ui:317-327`): it overlays
the content until 1100sp, then pins beside it. No single UI file in the eight
combines two split views. The purpose is clear, but it is the part of the shell most likely to
produce allocation surprises when the sidebar and the web column change state
together.

**5. There is no `AdwMultiLayoutView` or `AdwBottomSheet`.** Text Editor and
Shortwave use them because their wide and narrow chrome differ structurally.
Babble's narrow mode keeps the same `OverlaySplitView` and opens the sidebar
as an overlay, so a layout switcher would add a second shell for no gain.
Likewise, the profile and status views use `AdwViewStack` + `AdwViewSwitcher`
(`profile.py:426-429`, `statusviews.py:68-69`) without needing a top-level view
switcher.

**6. There is no Blueprint.** Five of the eight use Blueprint; Babble uses XML
plus Python. XML is not the limitation — Tuba has 40 templates, Text Editor 18
— but Babble's one template does not exercise that scale. Adding Blueprint now
would buy more compact expressions and breakpoint setters, at the cost of a
new build dependency and a second UI language. The corpus does not justify
that churn by itself.

### Findings, with evidence

Each item states what was observed, then what is not known. Nothing here is a
project decision; decisions on record are listed in the next section.

| # | Observation | Evidence | Not established |
|---|---|---|---|
| F1 | (Shipped in `ui/proposals-work`: the two bases are merged, so one base serves fourteen dialogs.) Two near-identical dialog bases existed; nine concrete dialogs used them | `ToastDialog(Adw.Dialog)` at `lists.py:127` (four users: `ListEditor`, `_MembershipDialog`, `AdvancedSearchDialog` `search.py:576`, `MultiHashtagEditor` `hashtags.py:448`) and `AdminDialogBase` at `admin.py:251` (five users). Both own a header, an `Adw.PreferencesPage` and an `Adw.ToastOverlay`; they differ in attribute name, toast timeout and one helper. AST walk: 21 `Adw.Dialog` classes = 2 bases + 9 on a base + 10 hand-built | Whether the hand-built dialogs fit a shared slot (see the implementation study) |
| F2 | Ten dialogs build the scaffold by hand (eight modules contain both `Adw.ToolbarView` and `Adw.ToastOverlay`) | `report.py:164-196`, `profileedit.py:236-239`, `wrapped.py`, `fedicollections.py`, `admin.py`, `notifications.py`, `compose.py`, `profile.py` each contain both `Adw.ToolbarView` and `Adw.ToastOverlay` | Whether their content shapes (`NavigationView`, `follows_content_size`) fit a shared slot |
| F3 | Toasts are routed by ancestor lookup, with one recorded exception | `pages.show_toast` prefers the overlay inside the dialog the widget lives in (`pages.py:398-425`); the composer keeps its own lookup (`compose.py:1359`) | Nothing checks that every dialog that raises a toast owns an overlay |
| F4 | `window.py` is 2,099 lines and a declared test exception | `test-file-size.py` `EXCEPTIONS`; `CLAUDE.md` still says ~1650 | Whether the figure matters; the test deliberately prints no number |
| F5 | The shell nests two `AdwOverlaySplitView`s; no single UI file in the eight combines two | `window.ui:317-327`; corpus scan of `*.blp`/`*.ui` | Whether this causes bugs; `window.py:1318-1330` documents one allocation re-entrancy it works around |
| F6 | `described-by` is unused; one `labelled-by` in XML and one `LABELLED_BY` in Python | `window.ui` and `catchup.py:1775`; 71 `update_property` calls outside tests | Screen-reader behaviour; no Orca run was done for this report |
| F7 | Babble has no legacy widget from the skill's deprecated list | Greps for `InfoBar`, `MessageDialog`, `TreeView`, `Leaflet`, `PreferencesWindow`, `get_style_context`, `FileChooser`, `ColorButton`, `ComboBoxText`, `EntryCompletion`, `ShortcutsWindow`, `Spinner`, `set_titlebar`: 0 hits | Anything outside that list |
| F8 | `style.css` has one reduced-motion block; none of the eight apps' sheets has one | `style.css:797`; `prefers-reduced-motion` grep over all eight (excluding demos): 0 | Whether the eight handle motion elsewhere (for example in code) |
| F9 | Only one template (`window.ui`) exists; dialogs, rows and the icon rail are built in Python | `grep @Gtk.Template` finds only `window.py:111` | Whether templating would reduce review or test friction |
| F10 | The media viewer uses an `Adw.Window` host on purpose | `mediaviewer.py:1666`; the `window.ui` comment says an `AdwDialog` cannot fill the window | n/a |

### Decisions already on record

These come from the project's own documents and should not be re-litigated by
this report.

- **The composer does not use `pages.show_toast`.** `CLEAN-03`
  (`Docs/IMPROVEMENTS.md`): `window_of()` returns the window for a widget in a
  dialog, and the composer is a dialog covering that window, so its overlay
  lookup stays local. The shared helper is correct for every other caller.
- **`window.py` is a recorded exception** to the 800-line rule: the remaining
  lines are view routing over page containers the window owns, and moving them
  would invert `streaming.py`'s dependency (`test-file-size.py`, `CLAUDE.md`).
- **The banners stay in a plain `Gtk.Box`, not an `AdwToolbarView` top bar**
  (`DEF-03`, deferred): the fix means restructuring the shell page.
- **No second stylesheet provider.** `style.css` is loaded by `AdwApplication`
  (`CLAUDE.md`); `app lifecycle` and `style css` tests enforce related rules.
- **`app.shortcuts` belongs to libadwaita**, pinned by `app lifecycle`.

### Roadmap cross-check

`Docs/ROADMAP.MD` lists the planned work. Only the items that touch UI
structure are compared; none of the corpus apps is a precedent for most of
them. Presence of `src/reader.py`, `palette.py`, `store.py` and `mpris.py` was
checked: none exists yet. `capabilities.py` and `sysstate.py` do.

| Roadmap item | What the code and the eight show |
|---|---|
| **Undo via toasts** (Phase 3, "cheap win #2") | Already partly in place: `pages.show_toast(undo=…)` sets an Undo button and `Adw.ToastPriority.HIGH` (`pages.py:406-409`); undo is used by the profile actions (follow, block, domain block; `profileactions.py:217-368`), the status menu (`statusmenu.py:238`) and hashtags (`hashtags.py:674`). Nautilus does the same with `ADW_TOAST_PRIORITY_HIGH` (`nautilus-window.c:909,939`). The composer exception (CLEAN-03) applies. |
| **Deck mode with `AdwTabView`, split views and `AdwBreakpoint`** (Phase 3) | Babble uses `AdwTabView` nowhere. Text Editor and Nautilus are the only corpus apps with `AdwTabView`; Text Editor wires `close-page`, `setup-menu` and `create-window` (tear-off to a new window, `editor-window.ui:64`) and Nautilus pairs it with `AdwTabOverview`. Neither combines tabs with nested split views, and Babble already nests two split views (F5). This is the roadmap item with the least corpus support and the most existing layout risk. |
| **Command palette** (Phase 3) | None of the eight apps has one (grep: 0). No precedent to benchmark; Babble's `push_page` navigation and `app.*`/`win.*` actions are the only available base. |
| **Reader mode** (Phase 1) | None of the eight has a reader (grep: 0). The planned `AdwNavigationPage` + `AdwToolbarView` + `GtkTextView` stack matches how `make_nav_page` already builds pages (`pages.py:329-361`). |
| **Reduced-motion and high-contrast passes** (Phase 6) | Babble already has five high-contrast blocks and one reduced-motion block (F8); the eight have none for reduced motion. The Orca/ATK audit is where F6 would be tested. |
| **Capability gating** (Phase 5) | Not a UI-structure topic; `capabilities.py` exists and a `capability gates` test guards it. |
| **Offline store, MPRIS, calm mode, export** | No UI-architecture claim can be drawn from the corpus. |

### Proposals

These are this report's suggestions, ordered by how much evidence supports
them. None is a project commitment.

1. **Merge the two bases and add a content slot (F1, F2).** *(Shipped in `ui/proposals-work`.)* Combine
   `ToastDialog` and `AdminDialogBase` into one class in `pages.py`, then make
   its content a slot rather than a fixed `PreferencesPage`. Fit analysis in
   the implementation study: `CollectionEditDialog`, `NotificationPolicyDialog`,
   `WrappedDialog`, `ProfileEditDialog` and `PrivateNoteDialog` fit;
   `ReportDialog`, `ProfileDialog`, `AdminDialog`, `ComposeDialog` and
   `SchedulePickerDialog` stay as they are. Precedent: Fractal's `ToastableDialog`
   (`src/components/dialogs/toastable.rs:18-25`).
2. **Add a test for toast ownership (F3).** Enumerate `Adw.Dialog`
   subclasses and assert each either owns an overlay or is on a short, named
   list of window-fallback exceptions (starting with the composer's documented
   case).
3. **Extend `test-style-css.py` to pin the clean state.** `@define-color`,
   `.dim-label`, `overlay-split-view` and `widget.background` are absent today
   (verified); the test would keep them absent.
4. **Test the 600/601sp and 1100sp breakpoint boundaries for geometry (F5).**
   `test-sidebar-state.py` covers derived state, not allocation. This also
   de-risks the roadmap's deck mode.
5. **Before starting deck mode, prototype tabs against the nested split
   views.** The corpus offers no combined example, so the layout behaviour is
   untested territory.
6. **Optionally extract one stable dialog to `.ui` (F9)** and compare review
   and test effort; stop if there is no gain. The eight do not require a UI
   file count.
7. **If an Orca pass is run (F6),** start with media attachments and boost
   strips, where a composed `described-by` is the most plausible addition.
8. **No change to versioning, the shell, the timeline model or the stylesheet
   is supported by this evidence.**

### Against the skill

On the legacy-API checks listed in F7, Babble matches the skill's
non-negotiable list more completely than any of the eight. Three things the
skill could take from Babble:

- **`AdwSidebar` is in use.** None of the eight uses it; Babble uses it for the
  full sidebar with a custom rail beside it (`window.ui:375`,
  `window.py:1402-1604`).
- **Code-first UI is a working mode.** One template, 69 modules, behaviour-heavy
  rows in Python. The skill assumes a declarative file per class; it could say
  where that stops being appropriate.
- **"One toast gateway" has two readings.** Babble has one dispatch path
  (`show_toast`) and per-dialog overlays, with a recorded exception (CLEAN-03).
  The skill should distinguish a single dispatch path from a single overlay.

The skill's `references/` were not re-read for this comparison; only
`SKILL.md` was.

### Summary

On the evidence gathered, Babble is not behind the eight on widgets,
navigation, lists, dialogs or CSS. The measurable gaps are two dialog bases with the
same shape and ten hand-built dialogs (F1, F2), no automated rule for toast ownership (F3), and
one template for the whole UI (F9). The roadmap item with the least precedent
and most layout risk is deck mode. Everything in "Proposals" is optional.
