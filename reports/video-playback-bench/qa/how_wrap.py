import os, sys, runpy, collections
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
root = os.getcwd(); sys.path.insert(0, root); sys.path.insert(0, root + '/src')
from src import mediaengine
hows = collections.Counter(); orig = mediaengine.Engine._finish
def _finish(self, how, immediate=False):
    if not self._finished:
        hows[how] += 1
    return orig(self, how, immediate)
mediaengine.Engine._finish = _finish
target = sys.argv[1]; sys.argv = [target]
try:
    runpy.run_path(target, run_name='__main__')
finally:
    print('HOW', target, dict(hows), flush=True)
