# Translating Babble

Babble's strings live in this directory. Four languages are shipped: `de`,
`es`, `fr` and `pt_BR`.

**All four catalogues are machine-assisted and need review by native
speakers.** You are welcome — corrections are the point. The
[fuzzy](../Docs/localization.md#translation-status) entries are machine
guesses carried over from an earlier catalogue merge; they are the highest
value thing to look at.

## Working on a catalogue

```sh
meson compile -C build babble-pot          # regenerate babble.pot
meson compile -C build babble-update-po    # merge into every *.po
meson compile -C build babble-po           # compile the .mo files
```

Edit `po/<lang>.po` directly — it is a normal gettext catalogue, and any
PO editor works.

Check your work before sending it:

```sh
msgfmt --check --check-format -o /dev/null po/<lang>.po
```

That is the same check CI runs, as the `po check <lang>` test. It catches a
translation whose format specifiers (`%s`, `%d`, …) do not match the
original — a mistake that would otherwise only surface as a runtime error.

## Adding a language

1. `msginit -l <lang> -i po/babble.pot -o po/<lang>.po`
2. Add `<lang>` to `po/LINGUAS`
3. Add it, named in its own language, to `INTERFACE_LANGUAGES` in
   `src/preferences.py` — this is what puts it in Preferences → General →
   Language
4. Translate it

## Conventions

**Format specifiers are not translated.** `%s`, `%d` and their flags stay
exactly as they are. Translating them produces a string that crashes at
runtime rather than one that looks wrong.

**Keep the trailing or leading space** some strings carry, so the button
label and the message still read correctly when concatenated.

**Comments beginning `Translators:`** explain a string that cannot be
translated literally. They are in the `.po` file right above the entry.

## Language notes

### German

German uses the plain form for person nouns — *Autor*, *Alle Autoren* — rather
than a gender-inclusive or gender-star form. The catalogue had no convention
for them, so this is a default rather than a settled decision, and German
reviewers are welcome to change it.
