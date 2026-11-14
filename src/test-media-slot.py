#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""EngineSlot: stream-before-load, stop keeps engine, release once."""
import os
import sys

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src.mediaengineslot import EngineSlot, PlaybackUnavailable  # noqa: E402

failures = []


def check(name, condition):
    print(('ok - ' if condition else 'FAIL - ') + name)
    if not condition:
        failures.append(name)


class RecordingEngine:
    def __init__(self, user_agent=None, gl=True):
        self.user_agent = user_agent
        self.gl = gl
        self.calls = []
        self.released = False

    def load(self, url):
        self.calls.append(('load', url))

    def stop(self):
        self.calls.append(('stop',))

    def release(self):
        self.calls.append(('release',))
        self.released = True


class RecordingStream:
    def __init__(self, engine):
        self.engine = engine
        engine.calls.append(('stream',))
        self.paused = False
        self.detached = False

    def pause(self):
        self.paused = True
        self.engine.calls.append(('pause',))

    def detach(self):
        self.detached = True
        self.engine.calls.append(('detach',))


def install(engine_cls=RecordingEngine, stream_cls=RecordingStream):
    """Put recording stand-ins where the real EngineSlot.start imports
    them from (``src.mediaengine``, ``src.mediastream``), so the real
    method runs and GStreamer is never loaded."""
    import types
    import src

    names = ('mediaengine', 'mediastream')
    saved = {name: (sys.modules.get('src.' + name),
                    getattr(src, name, None)) for name in names}
    fakes = {'mediaengine': types.ModuleType('src.mediaengine'),
             'mediastream': types.ModuleType('src.mediastream')}
    fakes['mediaengine'].Engine = engine_cls
    fakes['mediastream'].EngineStream = stream_cls
    for name, module in fakes.items():
        sys.modules['src.' + name] = module
        setattr(src, name, module)

    def restore():
        for name, (module, attribute) in saved.items():
            if module is None:
                sys.modules.pop('src.' + name, None)
            else:
                sys.modules['src.' + name] = module
            if attribute is None:
                if hasattr(src, name):
                    delattr(src, name)
            else:
                setattr(src, name, attribute)
    return restore


slot = EngineSlot()
restore = install()
stream = slot.start('https://example/v.mp4', user_agent='BabbleTest', gl=True)
check('stream is built before load',
      slot.engine.calls[:2] == [('stream',), ('load', 'https://example/v.mp4')])
check('start records user_agent and gl',
      slot.engine.user_agent == 'BabbleTest' and slot.engine.gl is True)
engine = slot.engine
slot.stop(stream)
check('stop pauses detaches and stops without release',
      stream.paused and stream.detached
      and ('stop',) in engine.calls and ('release',) not in engine.calls
      and slot.engine is engine)
stream2 = slot.start('https://example/w.mp4', user_agent='BabbleTest', gl=True)
check('second start reuses the engine', slot.engine is engine)
slot.release()
check('release clears once', slot.engine is None and engine.released)
calls = list(engine.calls)
slot.release()
check('second release is a no-op',
      slot.engine is None and engine.calls == calls)
restore()

# Failed start leaves no engine.
class BoomEngine(RecordingEngine):
    def __init__(self, user_agent=None, gl=True):
        super().__init__(user_agent=user_agent, gl=gl)
        raise RuntimeError('no gst')


slot = EngineSlot()
restore = install(engine_cls=BoomEngine)
try:
    slot.start('https://example/v.mp4', user_agent='x', gl=False)
    raised = False
except PlaybackUnavailable:
    raised = True
check('failed Engine construction raises PlaybackUnavailable', raised)
check('a failed Engine construction leaves no engine',
      slot.engine is None)
restore()


class BoomLoad(RecordingEngine):
    def load(self, url):
        raise ValueError('bad url')


slot = EngineSlot()
restore = install(engine_cls=BoomLoad)
try:
    slot.start('bad', user_agent='x', gl=True)
    raised = False
except PlaybackUnavailable:
    raised = True
check('failed load raises PlaybackUnavailable', raised)
check('a failed load keeps the engine and does not release it (the '
      'caller decides, as before the slot)',
      slot.engine is not None and not slot.engine.released)
restore()

if failures:
    print('%d failures' % len(failures))
    sys.exit(1)
print('ok - media slot complete')
