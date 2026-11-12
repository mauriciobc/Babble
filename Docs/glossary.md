# Glossary of domain terms

A working note for contributors. It names the concepts the code is built
around, in plain words, so that module names, test names and discussion use
the same term for the same thing. It makes no promise to a reader of the
app; it is not a formal item under the
[documentation policy](documentation-policy.md).

It is not the manual's glossary. [Chapter 12 of the user
manual](manual/12-glossary.md) (also in [Portuguese](pt_BR/manual/12-glossario.md))
defines terms for people who use Babble, in the words the app shows. This one
defines the ideas behind the code, and where both name the same thing (the
media preferences, for instance) they must agree; the manual's [section
5.8](manual/05-media.md) describes the same preferences from the reader's
side. As a working note, this one stays in English
([section 3.5 of the policy](documentation-policy.md)).

Entries are domain language only. Where to find the code is in `CLAUDE.md`.

**Media policy**
The single set of rules that answers "may this media load, or play, right
now?". It looks at the reader's settings, the desktop's motion settings and
the state of the connection and battery, and the same answer is used wherever
media appears.

**GIFV**
An animated GIF as Mastodon serves it: a short, silent, looping video. The
manual calls it an animated GIF.

**Autoload**
The Load Media Automatically choice: always, only on an unmetered connection,
or never. It covers video, audio and GIFVs (images always load). It decides
whether they start without a click. When they do not, the media viewer shows
the preview and a button that starts it; a GIFV in the timeline simply stays
still.

**Metered deferral**
Holding back a download that nobody asked for because the connection is
metered (it costs the reader data), when Autoload is set to not load on
metered connections. In the media viewer it applies only to the start of a
stream: a video the reader has started is not stopped when the connection
becomes metered. A GIFV playing in the timeline (Inline playback) is
different: it is stopped when the connection becomes metered and starts again
when it stops being metered, because nobody asked for that one to play.

**Autoplay of looping media**
A GIFV may run by itself only when the reader has Autoplay Animated Media on,
the desktop has animations on and no reduced motion requested, and the
computer is not saving power. When any of these changes, a clip that was
paused by the rule resumes, and one the reader paused stays paused.

**Reduced motion and power saving**
Two reasons looping media stays still: the reader asked the desktop to reduce
motion (or turned animations off), or the computer is in power-saver mode.
Both are read live.

**Remote media**
An attachment that the reader's own server does not hold, so only the author's
server has it. Fetching it tells that server the reader's address and that
they looked. The Media From Other Servers choice is to load it, to ask first,
or never; a copy on the reader's own server is always loaded.

**Inline playback**
Playing GIFVs in the timeline itself, one at a time (the one most in view),
instead of waiting for a click to open the viewer. It is off unless the reader
turns on Play Animated GIFs in Timelines, and it follows every rule above, live:
the playing GIFV stops when one of them stops allowing it and starts again
when they allow it.
Where the answer would be to ask about remote media, it does not play.

**Stream**
One live feed the server can push to the app over its single open connection:
the reader's own (home and notifications), the direct messages, the public feed
of this server or of everyone it knows, one list, or one hashtag. A page that is
open only updates live while the stream it follows is subscribed.

**Place's stream**
The stream a built place follows, if it has one. Home follows the reader's own
stream; Conversations follows the direct messages; Local and Federated follow
their public feeds, only while live public updates are on; a list follows its
own list stream; a hashtag page follows its tag's stream, but only while live
public updates are on, and a page over several tags follows none, because one
tag's stream cannot apply the server's rules for combining tags.

**Stream router**
The part that knows two things: which streams the open places want, and which
page should receive each event the server pushes. It looks pages up when an
event arrives and keeps none of them, so a page that was closed is not held
alive. It does not own the connection: opening and closing it, falling back to
polling when it is down, and catching up after it comes back stay with the
window.

**Request scope**
What one page owns of the reads it has asked the server for, and the one
question it asks when an answer comes back: is this still wanted? A page
starts a new generation of reads when it loads again, which cancels the ones
it asked for before; it closes the scope when it is really going away, which
cancels them all. Hidden is not going away: a place the reader has left stays
built so it can be shown again, and a read still out for it is kept and its
answer shown. An answer that is no longer wanted, or whose request was
cancelled, is dropped without a message, because the cancelling was the
program's own doing. A write is never part of a scope: cancelling cannot undo
it, because the server may already have acted.

**Superseded**
A read that a newer read of the same page has replaced: the page reloaded, or
the reader pressed Try Again, before the first answer came. Its answer is
ignored and, with a request scope, the request itself is cancelled.
