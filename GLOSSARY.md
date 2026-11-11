# Glossary

The words Babble uses for what the reader sees and chooses, as the code means
them.

**Media policy**
The single set of rules that answers "may this media load, or play, right
now?". It looks at the reader's settings, the desktop's motion settings and
the state of the connection and battery, and the same answer is used wherever
media appears.

**Autoload**
The Load Media Automatically choice: always, only on an unmetered connection,
or never. It decides whether a video, audio clip or GIFV starts without a
click; when it does not, the reader sees the preview and a button that starts
it.

**Metered deferral**
Holding back a download that nobody asked for because the connection is
metered (it costs the reader data). It applies only to the start of a stream:
something already playing is not stopped when the connection becomes metered.

**Autoplay of looping media**
A GIFV (a short, silent, looping clip) may run by itself only when the reader
has Autoplay Animated Media on, the desktop has animations on and no reduced
motion requested, and the computer is not saving power. When any of these
changes, a clip that was paused by the rule resumes, and one the reader paused
stays paused.

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
