import os
q = 'reports/video-playback-bench/qa/'
srcs = {n: open('src/%s.py' % n).read() for n in ('mediaengine', 'mediastream')}
open(q + 'sandbox_play.py', 'w').write('SOURCES = %r\n' % srcs + open(q + 'sandbox_body.py').read())
