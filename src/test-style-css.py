# SPDX-License-Identifier: GPL-3.0-or-later

"""style.css through a real GtkCssProvider, plus the rules it must keep.

Two halves, and the second one is only half a test.

The first loads the file the way the app does -- through a
Gtk.CssProvider -- and reports anything GTK rejects. This is the check
that matters, because a stylesheet that fails to parse is not a
partial feature: GTK drops the rules it could not read, so a typo in a
selector takes a preference down silently, with nothing in the app but
a warning on stderr. Run with G_DEBUG=fatal-warnings (the meson
environment sets it for every test) so a warning that is not a parse
error is fatal too. Parsing errors themselves are collected through
the `parsing-error` signal, which GTK 4.24 does emit, so they arrive
as a FAIL line here instead of as an abort.

The second loads the same path a second time, out of the compiled
gresource bundle, because that is the copy the installed app reads.
The bundle is built by `meson compile` and is not rebuilt here: a stale
bundle is a fact about the tree, not a failure of this file, so the
stale case prints a note and the missing case prints a skip. Anything
the two copies disagree about is printed, which is how a rule that was
edited and never recompiled gets noticed.

The rest is the content the accessibility fixes landed on, asserted so
they cannot quietly go back: reduced motion, the disabled-opacity
helper, the text-size presets on the window node, the hoisted
lightbox foreground, and the wallpaper dropped in high contrast.
"""

import glob
import os
import re
import sys

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, Gtk  # noqa: E402

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
srcdir = os.path.abspath(srcdir)
root = os.path.dirname(srcdir)
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(root, 'build')

CSS = os.path.join(srcdir, 'style.css')
RESOURCE_PATH = '/io/github/mauriciobc/Babble/style.css'

failures = 0


def check(label, condition, detail=''):
    global failures
    if condition:
        print('ok - %s' % label)
    else:
        failures += 1
        print('FAIL - %s' % label)
    for line in (detail if isinstance(detail, list) else [detail]):
        if line:
            print('    %s' % line)


def note(label, detail=''):
    print('    %s%s' % (label, (': ' + detail) if detail else ''))


def media_body(text, condition):
    """The body of ``@media (<condition>) { ... }``, matched by brace count.

    Returns None when there is no such block at all, so the checks can
    tell 'no block' from 'a block without this rule in it'. The file
    has more than one high-contrast block, which is why the companion
    rules are looked up with media_bodies().
    """
    bodies = media_bodies(text, condition)
    return bodies[0] if bodies else None


def media_bodies(text, condition):
    """Every ``@media (<condition>) { ... }`` body, in source order."""
    bodies = []
    opening = -1
    while True:
        start = text.find('@media (%s)' % condition, opening + 1)
        if start < 0:
            return bodies
        opening = text.index('{', start)
        depth = 0
        for index in range(opening, len(text)):
            if text[index] == '{':
                depth += 1
            elif text[index] == '}':
                depth -= 1
                if depth == 0:
                    bodies.append(text[opening + 1:index])
                    break


def strip_comments(text):
    """The text without its /* */ comments.

    The comments quote the colours they replaced, so a check that
    counted them as declarations would report the explanation as the
    offence.
    """
    return re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)


def load_errors(provider, load, text=''):
    """Every parse error ``load`` reports, as printable lines.

    GtkCssLocation gives the line and a byte offset into the buffer, not
    a column, so the column is worked out here from the text that was
    loaded -- which also means the message points somewhere a reader can
    go, rather than at an offset they have to convert themselves.
    """
    errors = []

    def on_parsing_error(_provider, section, error):
        location = section.get_start_location()
        line = location.lines + 1
        start = text.rfind('\n', 0, location.chars)
        column = location.chars - start
        errors.append('line %d column %d: %s'
                      % (line, column, error.message))

    handler = provider.connect('parsing-error', on_parsing_error)
    try:
        load(provider)
    finally:
        provider.disconnect(handler)
    return errors


with open(CSS, encoding='utf-8') as style_file:
    css = style_file.read()

provider = Gtk.CssProvider()
errors = load_errors(provider, lambda p: p.load_from_path(CSS), css)
check('style.css loads through Gtk.CssProvider', not errors, errors)

# The compiled bundle, when there is one. Never rebuilt here: a stale
# copy is reported, not fixed, because rebuilding is `meson compile`'s
# job and the build directory belongs to whoever is running it.
bundles = sorted(glob.glob(os.path.join(builddir, '**', 'babble.gresource'),
                           recursive=True))
if not bundles:
    print('skip - %s has no babble.gresource, so the bundle copy of '
          'style.css goes unchecked' % os.path.relpath(builddir, root))
    note('meson compile -C %s builds it'
         % os.path.relpath(builddir, root))
else:
    note('bundle', os.path.relpath(bundles[0], root))
    resource = Gio.Resource.load(bundles[0])
    Gio.resources_register(resource)
    shipped = Gio.resources_lookup_data(RESOURCE_PATH,
                                        Gio.ResourceLookupFlags.NONE)
    if shipped is None:
        note('the bundle carries no %s, so there was nothing to load'
             % RESOURCE_PATH)
    else:
        packed = shipped.get_data()
        bundled = Gtk.CssProvider()
        errors = load_errors(
            bundled,
            lambda p: p.load_from_resource(RESOURCE_PATH),
            packed.decode('utf-8'))
        check('%s loads from the gresource bundle' % RESOURCE_PATH,
              not errors, errors)
        if packed == css.encode('utf-8'):
            note('the bundle copy is the same file')
        else:
            # Not a failure: the bundle is a build product and this test
            # is not allowed to rebuild it. It is printed because a rule
            # that was edited and never recompiled is invisible at
            # runtime, and the two copies then ship different rules.
            note('the bundle copy is stale, so the installed app still '
                 'runs the older rules until it is recompiled')

# -- Reduced motion ----------------------------------------------------
reduced = media_body(css, 'prefers-reduced-motion: reduce')
check('there is a prefers-reduced-motion block', reduced is not None,
      'GTK 4.22+ matches it, and the file already uses '
      'prefers-contrast the same way')
for selector in ('window.scale-emoji-hover .custom-emoji',
                 '.dim-enabled .notification-row.can-be-dimmed',
                 '.compose-counter', 'label.just-toggled', '.fade-in'):
    check('reduced motion covers %s' % selector,
          reduced is not None and selector in reduced)
if reduced is not None:
    check('reduced motion stops the hover scale',
          'transform: none' in reduced)
    check('reduced motion stops the dimmed-image filter transition',
          re.search(r'window\.darken-media \.darkenable\s*\{\s*'
                    r'transition:\s*none', reduced) is not None)
    check('reduced motion stops the one-shot keyframes',
          re.search(r'animation:\s*none', reduced) is not None)
    check('reduced motion says what it does not cover',
          'gtk-enable-animations' in css,
          'GTK honours that for CSS and libadwaita reads it itself')

# -- Disabled opacity --------------------------------------------------
check('the disabled buttons use the libadwaita helper',
      css.count('var(--disabled-opacity)') >= 2)
for literal in ('opacity: 0.35', 'opacity: 0.4'):
    check('%s is gone' % literal, literal not in css)

# -- Text size on the window node --------------------------------------
for preset, size in (('text-small', '0.9em'), ('text-large', '1.1em')):
    check('window.%s declares font-size: %s' % (preset, size),
          re.search(r'window\.%s\s*\{\s*font-size:\s*%s\s*;'
                    % (preset, re.escape(size)), css) is not None)
    # The old rules scaled labels only, so every other text node --
    # entries, TextView bodies, the composer's buffer -- kept the
    # default size. Inheritance off the window node is the fix.
    check('window.%s does not scale labels alone' % preset,
          re.search(r'window\.%s\s+label' % preset, css) is None)
check('the text sizes are in em, so GTK text scaling composes with them',
      not re.search(r'window\.text-(small|large)[^{]*\{[^}]*font-size:\s*\d+%',
                    css))
check('window.large-line-height declares line-height on the window',
      re.search(r'window\.large-line-height\s*\{\s*line-height:\s*1\.4',
                css) is not None)
check('window.large-line-height does not name .inline-emoji-text',
      re.search(r'window\.large-line-height\s+label', css) is None)

# -- Lightbox foregrounds ----------------------------------------------
check('--viewer-fg is declared on .media-viewer-content',
      re.search(r'\.media-viewer-content\s*\{[^}]*--viewer-fg:\s*#fff',
                css) is not None)
check('--viewer-fg is used for the lightbox foregrounds',
      css.count('var(--viewer-fg)') >= 8)
# The lightbox rules, from the exempt card to the profile slice. Cut at
# a rule rather than at the section comment, so the slice still starts
# outside a comment block and strip_comments() can see the boundaries.
viewer = css[css.index('.media-viewer-card {'):css.index('.profile-chip {')]
check('no hard-coded #fff is left in the lightbox rules',
      strip_comments(viewer).count('#fff') == 1
      and '--viewer-fg: #fff;' in viewer,
      'only the custom property may hold the white now')
check('the lightbox surface exemption is kept',
      re.search(r'\.media-viewer-card\s*\{\s*background-color:\s*#000',
                css) is not None)
# The file has several high-contrast blocks, so the lightbox companion
# is the one that names the lightbox rather than the first of them.
companion = [body for body in media_bodies(css, 'prefers-contrast: more')
             if '.media-viewer-card' in body]
check('the lightbox has a high-contrast companion',
      bool(companion)
      and '--viewer-fg: var(--window-fg-color)' in companion[0])
check('the high-contrast lightbox gives the zoom bar a surface',
      bool(companion) and '.media-viewer-zoom' in companion[0]
      and 'var(--card-bg-color)' in companion[0])
check('no literal white alpha is left in the lightbox rules',
      'rgba(255, 255, 255' not in strip_comments(viewer))
check('the Catch Up scrim and on-image text are left alone',
      '.catchup-on-image' in css and '.catchup-scrim' in css
      and 'var(--viewer-fg)' not in css[css.index('.catchup-scrim'):])
# opacity is a number or a percentage, never both: GTK drops a max()
# that mixes the two -- "Inconsistent types in 'max('" -- and the
# caption then keeps full strength on the photo scrim. --dim-opacity is
# a percentage (55%, and 90% under high contrast), so the floor has to
# be one too.
check('the on-image caption dim compares like with like',
      re.search(r'opacity:\s*max\(\s*\d+%\s*,\s*var\(--dim-opacity\)\s*\)',
                strip_comments(css)) is not None)

# -- The timeline canvas is a plain colour -----------------------------
# A background picture or gradient is rasterised before the first card
# (a full-size PNG decoded again on every stylesheet parse), so the
# canvas stays a flat colour.
canvas = re.search(r'\.timeline-list,\s*\.notification-list-scroller\s*\{'
                   r'[^}]*\}', css)
check('the timeline canvas has no background image',
      canvas is not None and 'background-image' not in canvas.group(0)
      and 'url(' not in canvas.group(0))
check('the canvas mix is untouched',
      re.search(r'\.timeline-list,\s*\.notification-list-scroller\s*\{'
                r'[^}]*color-mix\(in srgb,\s*var\(--window-bg-color\) 92%',
                css) is not None)

if failures:
    print('%d checks failed' % failures)
    sys.exit(1)
print('all style.css checks passed')
