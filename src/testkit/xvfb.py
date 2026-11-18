# SPDX-License-Identifier: GPL-3.0-or-later

"""A private Xvfb for the tests that need a real X11 window.

``start_xvfb()`` must run before ``import gi``: the display it finds goes
into ``DISPLAY``, and GDK reads that when it initialises. Test-only; never
installed.
"""

import atexit
import os
import subprocess
import time

_xvfb = None


def _dies_with_us():
    # Runs in the child before exec: the kernel sends Xvfb SIGTERM when the
    # test process dies, however it dies (an exception, meson's timeout,
    # SIGKILL), so an Xvfb can never outlive the test.
    import ctypes
    import signal
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)


def _stop():
    if _xvfb is not None and _xvfb.poll() is None:
        _xvfb.terminate()
        try:
            _xvfb.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _xvfb.kill()
            _xvfb.wait()


atexit.register(_stop)


def start_xvfb():
    """Find (or start) a free X server and point DISPLAY at it."""
    global _xvfb
    if os.environ.get('BABBLE_KEEP_DISPLAY'):
        return
    for n in range(81, 90):
        if os.path.exists('/tmp/.X11-unix/X%d' % n):
            continue
        _xvfb = subprocess.Popen(
            ['Xvfb', ':%d' % n, '-screen', '0', '1280x1024x24'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=_dies_with_us)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline \
                and not os.path.exists('/tmp/.X11-unix/X%d' % n):
            time.sleep(0.05)
        if os.path.exists('/tmp/.X11-unix/X%d' % n):
            os.environ['DISPLAY'] = ':%d' % n
            return
        _xvfb.terminate()
        _xvfb = None
