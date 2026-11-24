# Third-party Python libraries (Feed Chef)

Feed Chef uses four optional Python libraries that Babble does not vendor.
They are installed from PyPI (CI), from pinned wheels (the local development
Flatpak manifest, module `python-deps`) or by the distribution. Without
them the feature degrades and its tests skip (`src/feedchef/deps.py`; the
build reports which are present with a `message()`, never an error).

The vendored sentiment engines (VADER, LeIA) are recorded separately in
`src/feedchef/vendor/NOTICE`.

Licences below were read from the installed package metadata
(`importlib.metadata`) in the development virtual environment.

| Library | Version | Licence (metadata) | Used for |
|---|---|---|---|
| numpy | 2.5.5 | BSD-3-Clause (the wheel also bundles code under 0BSD, MIT, Zlib and CC0-1.0) | vector maths for language scoring and taste clusters |
| py3langid | 0.4.0 | BSD-3-Clause (its bundled model is from langid.py, BSD-2-Clause, by Marco Lui) | language identification |
| kmedoids | 0.5.5 | GPL-3.0-or-later | taste clustering |
| segno | 1.6.6 | BSD-3-Clause (classifier: OSI Approved :: BSD License; the `LICENSE` file ships in the wheel) | QR codes for sharing a recipe |

Compatibility: Babble is GPL-3.0-or-later, so linking kmedoids
(GPL-3.0-or-later) is compatible, and the BSD and MIT-style licences above
are permissive. A redistributed bundle (the Flatpak) must carry each
library's licence file; the wheels do, under `*.dist-info/licenses/`.

Pins: `py3langid==0.4.0`, `kmedoids==0.5.5`, `segno==1.6.6` in
`.github/workflows/ci.yml`; numpy comes from the distribution there and is
pinned to 2.5.5 in `build-aux/flatpak/io.github.mauriciobc.Babble.json`
(local development only, GNOME 51 runtime, Python 3.14, `cp314` wheels).
