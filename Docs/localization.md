# Localization

Babble speaks Brazilian Portuguese, German, French and Spanish. The
interface follows your system language unless you pick one in
Preferences → General → Language, which takes effect the next time Babble
starts.

## Translation status

Generated from `msgfmt --statistics`. **All four catalogues are
machine-assisted and still need review by native speakers** — the identical
counts below are a sign of that, not of four careful translations.

| Language | Translated | Fuzzy | Untranslated |
|---|---|---|---|
| `de` | 1431 | 52 | 29 |
| `es` | 1431 | 52 | 29 |
| `fr` | 1431 | 52 | 29 |
| `pt_BR` | 1431 | 52 | 29 |

Fuzzy entries are machine guesses carried over from an earlier run of
`babble-update-po`. They are not used at runtime, and they are the highest
value thing for a reviewer to look at.

## Flatpak

Babble's own translations ship inside the app (`"separate-locales": false`),
so every language in the picker works no matter what your system language is.
The few strings that come from GTK and libadwaita live in the runtime's locale
extension, which only installs your system's languages. To get those too, add
the language to Flatpak and update:

```sh
flatpak config --set extra-languages de
flatpak update
```

## Working on the catalogues

After changing strings:

```sh
meson compile -C build babble-pot          # regenerate po/babble.pot
meson compile -C build babble-update-po    # merge it into every po/*.po
```

To add a language, create `po/<lang>.po` from the template, list it in
`po/LINGUAS`, and add it — named in its own language — to
`INTERFACE_LANGUAGES` in `src/preferences.py`.

Every source file containing translatable strings is listed in
`po/POTFILES.in`. A new module with strings in it has to go there too, or its
strings never reach the catalogue.

The `po check <lang>` tests run `msgfmt --check-format`, which catches a
translation whose format specifiers do not match its original — a mistake
that would otherwise only show up at runtime.

See [po/README.md](../po/README.md) for notes aimed at translators.
