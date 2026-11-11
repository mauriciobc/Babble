# Glossary of domain terms

A working note for contributors. It names the concepts the code is built
around, in plain words, so that module names, test names and discussion use
the same term for the same thing. It makes no promise to a reader of the
app; it is not a formal item under the
[documentation policy](documentation-policy.md).

It is not the manual's glossary. [Chapter 12 of the user
manual](manual/12-glossario.md) defines terms for people who use Babble, in
Portuguese, in the words the app shows. This one defines the ideas behind the
code, in English, and where both name the same thing (the media preferences,
for instance) they must agree; the manual's [section
5.8](manual/05-midia.md) describes the same preferences from the reader's side.

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
whether they start without a click; when they do not, the reader sees the
preview and a button that starts it.

**Metered deferral**
Holding back a download that nobody asked for because the connection is
metered (it costs the reader data). It applies only to the start of a stream:
something already playing is not stopped when the connection becomes metered.

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
turns on Play Animated GIFs in Timelines, and it follows every rule above.
Where the answer would be to ask about remote media, it does not play.
