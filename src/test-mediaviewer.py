#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from gi.repository import Gio

from src.mediaviewer import _texture_error, download  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


err = _texture_error('invalid URL')
check('invalid URL is INVALID_ARGUMENT',
      err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.INVALID_ARGUMENT))
err = _texture_error('HTTP 404')
check('HTTP reason is translated',
      '404' in err.message and err.matches(Gio.io_error_quark(),
                                            Gio.IOErrorEnum.FAILED))

downloaded = []
download('not a url', Gio.File.new_for_path('/tmp/babble-download-unused'),
         on_done=downloaded.append)
check('an invalid download address is reported, not raised',
      len(downloaded) == 1 and downloaded[0] is not None
      and downloaded[0].matches(Gio.io_error_quark(),
                                Gio.IOErrorEnum.INVALID_ARGUMENT))

if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
