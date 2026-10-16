#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""What the instance advertises it takes, and refusing before reading.

The reference point is Mastodon's own server, which validates a file
against VIDEO_LIMIT for anything it classes as video or audio and
IMAGE_LIMIT for an image, strictly, so a file of exactly the limit is
refused. Two clients get it differently wrong:

* Tuba tests the 'image/' and 'video/' prefixes, so audio is never
  measured against anything;
* Mastodon's Android client tests !type.startsWith("image/"), so an
  image is never measured at all, and its comparison is '>' where the
  server's is a strict 'less_than'.

The table at the bottom is the point of this file: every cell from the
server's rule rather than from a client.
"""

import os
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))

from src.composeupload import (attachment_refusal, human_size,  # noqa: E402
                               size_limit_for, uses_video_size_limit)
from src.mastodon import (DEFAULT_MAX_MEDIA_ATTACHMENTS,  # noqa: E402
                          MastodonClient)

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s%s' % (name, (' :: %s' % detail) if detail else ''))


def client_with(configuration):
    client = MastodonClient('https://example.test')
    client.instance_info = {'configuration': configuration} \
        if configuration is not None else None
    return client


IMAGE = 16777216   # MediaAttachment::IMAGE_LIMIT,  16.megabytes
VIDEO = 103809024  # MediaAttachment::VIDEO_LIMIT,  99.megabytes

FULL = {
    'statuses': {'max_media_attachments': 6, 'max_characters': 1000},
    'media_attachments': {
        'image_size_limit': IMAGE,
        'video_size_limit': VIDEO,
        'supported_mime_types': ['image/jpeg', 'video/mp4', 'image/png'],
    },
}

# --- reading the limits ---

client = client_with(FULL)
check('the attachment count is read', client.max_media_attachments() == 6,
      str(client.max_media_attachments()))
check('the image size limit is read',
      client.media_size_limits() == (IMAGE, VIDEO),
      str(client.media_size_limits()))
check('the supported types are read',
      client.supported_media_types() == ['image/jpeg', 'video/mp4',
                                        'image/png'],
      str(client.supported_media_types()))

# --- and what happens when the instance says nothing ---

for label, configuration in (('no configuration', None),
                             ('no media_attachments', {'statuses': {}}),
                             ('empty media_attachments',
                              {'media_attachments': {}})):
    client = client_with(configuration)
    check('%s leaves the count at Mastodon\'s default' % label,
          client.max_media_attachments() == DEFAULT_MAX_MEDIA_ATTACHMENTS)
    check('%s leaves the sizes unset' % label,
          client.media_size_limits() == (None, None))
    check('%s leaves the types unset' % label,
          client.supported_media_types() is None)

for bad in (0, -1, None, 'lots', [4]):
    client = client_with({'statuses': {'max_media_attachments': bad}})
    check('a count of %r falls back' % (bad,),
          client.max_media_attachments() == DEFAULT_MAX_MEDIA_ATTACHMENTS,
          str(client.max_media_attachments()))

for bad in (0, -1, None, 'big'):
    client = client_with({'media_attachments': {'image_size_limit': bad}})
    image, _video = client.media_size_limits()
    check('an image limit of %r is not a limit' % (bad,), image is None,
          repr(image))

client = client_with({'media_attachments': {'video_size_limit': VIDEO}})
check('one limit can be known without the other',
      client.media_size_limits() == (None, VIDEO),
      str(client.media_size_limits()))

# The one case where following the instance would be actively harmful.
client = client_with(
    {'media_attachments': {'supported_mime_types':
                           ['application/octet-stream']}})
check('octet-stream alone is not a filter', client.supported_media_types()
      is None, str(client.supported_media_types()))
client = client_with({'media_attachments': {'supported_mime_types': 'image'}})
check('a non-list of types is not a filter',
      client.supported_media_types() is None)
client = client_with({'media_attachments':
                      {'supported_mime_types': ['image/png', 7, None]}})
check('non-strings are dropped from the filter',
      client.supported_media_types() == ['image/png'],
      str(client.supported_media_types()))

# --- the server's predicate, which neither reference client has ---

for mime, larger in (('video/mp4', True), ('video/quicktime', True),
                     ('audio/mpeg', True), ('audio/flac', True),
                     # An animated GIF is uploaded as image/gif and
                     # transcoded server-side; gifv is a classification
                     # the client never sends.
                     ('image/gif', False), ('image/png', False),
                     ('image/jpeg', False), ('VIDEO/MP4', True),
                     (None, False), ('', False)):
    check('%r is%s held to the video limit'
          % (mime, '' if larger else ' not'),
          uses_video_size_limit(mime) is larger)

# A MIME Mastodon refuses outright: content_type is validated against
# IMAGE_MIME_TYPES + VIDEO_MIME_TYPES + AUDIO_MIME_TYPES and image/gifv
# is in none of them, so no rule should have been written for it.
check('a gifv MIME is treated as the image it never arrives as',
      uses_video_size_limit('image/gifv') is False)

limits = (IMAGE, VIDEO)
for mime, expected, label in (
        ('image/png', IMAGE, 'image'),
        ('image/gif', IMAGE, 'animated gif'),
        ('video/mp4', VIDEO, 'video'),
        ('audio/mpeg', VIDEO, 'audio')):
    check('a %s is held to the %s limit' % (mime, label),
          size_limit_for(mime, limits) == expected,
          str(size_limit_for(mime, limits)))

check('an unknown size limit enforces nothing',
      size_limit_for('image/png', (None, None)) is None)
check('a zero limit enforces nothing',
      size_limit_for('image/png', (0, 0)) is None)

check('sizes read as a reader would say them',
      human_size(IMAGE) == '16 MB' and human_size(VIDEO) == '99 MB'
      and human_size(512 * 1024) == '512 kB' and human_size(1536) == '2 kB',
      '%s | %s' % (human_size(IMAGE), human_size(1536)))

# --- refusing before reading ---

client = client_with(FULL)
check('a file under the limit is not refused',
      attachment_refusal(client, 'image/png', IMAGE - 1,
                         'small.png') is None)
# validates_attachment_size is less_than, so the limit itself is too big.
refusal = attachment_refusal(client, 'image/png', IMAGE, 'exact.png')
check('a file exactly at the limit is refused', refusal is not None)
check('the refusal names the file', 'exact.png' in (refusal or ''),
      repr(refusal))
check('the refusal states the limit', '16 MB' in (refusal or ''),
      repr(refusal))
check('a file over the limit is refused',
      attachment_refusal(client, 'image/png', IMAGE + 1, 'big.png')
      is not None)

# 16 MB + 1 is over the image limit and under the video limit, so which
# limit a mime is measured against is the whole of the difference: Tuba
# and the Android client each get one of these two backwards.
between = IMAGE + 1
check('an image at 16 MB + 1 is over its limit',
      attachment_refusal(client, 'image/png', between, 'f.png') is not None)
check('an animated gif is held to the image limit too',
      attachment_refusal(client, 'image/gif', between, 'f.gif') is not None)
check('video at 16 MB + 1 is under its own limit',
      attachment_refusal(client, 'video/mp4', between, 'f.mp4') is None)
check('audio at 16 MB + 1 is under the video limit, not skipped',
      attachment_refusal(client, 'audio/mpeg', between, 'f.mp3') is None)
check('and both are over the video limit once past it',
      attachment_refusal(client, 'video/mp4', VIDEO, 'f.mp4') is not None
      and attachment_refusal(client, 'audio/mpeg', VIDEO, 'f.mp3')
      is not None)

silent = client_with(None)
check('an instance that says nothing refuses nothing',
      attachment_refusal(silent, 'video/mp4', VIDEO * 8, 'huge.mp4')
      is None)

# --- the whole matrix, from the server rather than from a client ---
#
# image -> IMAGE_LIMIT, video and audio -> VIDEO_LIMIT. One byte under
# each must pass and one byte over must not, which is what a strict
# less_than validation does.

matrix = (('image/png', IMAGE), ('image/gif', IMAGE),
          ('video/mp4', VIDEO), ('audio/mpeg', VIDEO))
for mime, server_limit in matrix:
    check('%s passes one under and fails at the server limit' % mime,
          attachment_refusal(client, mime, server_limit - 1, 'f',
                             (IMAGE, VIDEO)) is None
          and attachment_refusal(client, mime, server_limit, 'f',
                                 (IMAGE, VIDEO)) is not None)

if failures:
    print('%d failure(s): %s' % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('all media limit checks passed')
