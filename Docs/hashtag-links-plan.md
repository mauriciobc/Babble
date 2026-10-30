# Hashtag links: benchmark and plan

Status: implemented on `feat/hashtag-links` (2026-10-30).

Clicking `#tag` in a post opens the browser instead of Babble's hashtag
timeline. This document establishes why, compares Phanpy and Tuba, and
plans the fix on top of the `HashtagPage` that already exists.

Sources read:

| Client | Revision | Files |
|---|---|---|
| Phanpy (Preact) | `a736225` (2026-10-29) | `src/utils/handle-content-links.js`, `src/pages/hashtag.jsx`, `src/components/status-tags.jsx`, `src/app.jsx` |
| Tuba (Vala, GTK 4) | `0b19288` (2026-10-29) | `src/Widgets/RichLabel.vala`, `src/Widgets/HashtagBar.vala`, `src/Utils/TagExtractor.vala`, `src/Widgets/MarkupView.vala`, `src/Views/Hashtag.vala` |
| Babble | `37abd14` (2026-10-29) | `src/pages.py`, `src/statustext.py`, `src/textemoji.py`, `src/statusrow.py`, `src/notifications.py`, `src/hashtags.py`, `src/profile.py` |

## 0. The bug, reproduced

Babble *does* have an internal handler: `pages.open_link` recognises
`/tags/<name>` and `/tag/<name>` and calls `hashtags.push_hashtag`, which
pushes `HashtagPage` (timeline, Follow, Feature on Profile, Pin, usage
stats). But the handler sits behind a guard that rejects a real fraction
of real hashtag links, so from the reader's seat it is effectively absent.

The guard (`src/pages.py:1037-1044`) is:

```python
tag = _TAG_PATH.match(parts.path)
if tag:
    name = urllib.parse.unquote(tag.group(1))
    known = {t.get('name', '').lower() for t in tags or ()}
    if not tags or name.lower() in known:
        push_hashtag(widget, client, name)
        return
```

Four things make it fail:

1. **The parser throws the anchor class away.** `_ContentParser`
   (`src/statustext.py:53-77`) keeps only `href` and text. The one signal
   both reference clients use — `class="hashtag"` — never reaches the
   click handler.
2. **Mastodon stores a normalized tag name, the href keeps the author's
   spelling.** `status.tags[].name` is casefolded and transliterated;
   the href is percent-encoded from the displayed text. The equality
   test then fails and the link falls through to `open_web`, which with
   the default `open-links-in-app=false` launches the browser.
3. **Not every server links a hashtag through `/tags/`.** Wafrn uses
   `/dashboard/search/photo%20friday`; GoToSocial uses
   `/feeds/search?q=%23Erzählcafé`. Those never match `_TAG_PATH`.
4. **`NotificationPost` bypasses `open_link` entirely.** It renders
   Pango markup into a plain `Gtk.Label` (`src/notifications.py:735-740`,
   `787-810`) with no `activate-link` handler, so GTK's default opens
   every link externally, hashtags included.

Evidence, from 560 posts pulled from 14 tag timelines on
`mastodon.social` (2026-10-30): 547 carried hashtag links; **19 posts
(3.4%) had at least one link that falls through to the browser**. Of the
27 offending links, 22 are the normalization mismatch, 2 are non-`/tags/`
paths, and 3 are transliteration failures (`ð` → `d`, which Unicode
NFKD does not do):

| Displayed link | `status.tags[].name` | href path | Why the guard fails |
|---|---|---|---|
| `#KünstlicheIntelligenz` | `kunstlicheintelligenz` | `/tags/K%C3%BCnstlicheIntelligenz` | casefold + umlaut fold |
| `#Adás` | `adas` | `/tags/Ad%C3%A1s` | accent fold |
| `#tíðindi` | `tidindi` | `/tags/t%C3%AD%C3%B0indi` | `ð` → `d` |
| `#photo-friday` | `photofriday` | `/dashboard/search/photo%20friday` | hyphen removed, non-`/tags/` |
| `#Erzählcafé` | `erzahlcafe` | `/feeds/search?q=%23Erzählcafé` | accent fold, non-`/tags/` |

The tag timeline itself accepts either spelling — `/api/v1/timelines/tag/
KünstlicheIntelligenz` and `.../kunstlicheintelligenz` return the same
post — so the fix is only about deciding *that* a click is a hashtag,
not about resolving it.

## 1. Benchmark

### 1.1 Side by side

| Aspect | Phanpy | Tuba | Babble today |
|---|---|---|---|
| Detection signal | `class="hashtag"`, or the character before the link is `#`/`＃` | URL shape: the parent directory of the path is `tags` | URL shape (`/tags/`, `/tag/`) **and** membership in the status `tags` payload |
| Tag name taken from | The link's **display text**, minus the leading `#` | The URL path's last segment | The URL path, compared case-insensitively with `tags[].name` |
| Remote-instance links | Ignored; the name is what matters | Accepted for any host | Accepted for any host |
| Non-Mastodon backends | Works (name from text) | Fails (path is not `/tags/`) | Fails (path is not `/tags/`) |
| Target | `#/t/<tag>` SPA route (or `#/<instance>/t/<tag>`) | `Views.Hashtag` pushed on the window stack | `HashtagPage` pushed on the navigation stack |
| Tag page | Timeline, media-only filter, follow/unfollow, feature/unfeature, add/remove tags, shortcuts, switch instance, pagination, since-id updates, access gating | Timeline, follow/unfollow, feature on profile, hashtag streaming | Timeline, follow/unfollow, feature/unfeature, pin, usage stats, multi-tag definitions |
| Extra chips under a post | `status.tags` not already visible in the content | A trailing paragraph consisting only of hashtag links, max 3 + “Show N More” | None |
| Modifier / middle click | Left to the browser (new tab) | n/a (native) | n/a |
| Failure mode | None found | Hashtags from Wafrn/GTS go to the browser | 3.4% of real hashtag links go to the browser; notification previews always do |

### 1.2 Details

**Phanpy.** A delegated click handler on status content
(`handle-content-links.js:103-112`) intercepts when the anchor has
`class="hashtag"` or the preceding text ends in `#`. It reads the tag
from `innerText`, strips the leading `#`, and assigns
`location.hash = '#/t/<tag>'` (or `#/<instance>/t/<tag>` when browsing
another server's timeline). The href host is never consulted, which is
what makes remote and non-Mastodon content work. The route is
`/:instance?/t/:hashtag` (`app.jsx:894`). The hashtag page
(`pages/hashtag.jsx`) accepts up to five tags separated by `+` or spaces,
offers media-only, follow/unfollow, feature/unfeature, add/remove tag,
hashtag shortcuts, switching server, `max_id` paging and `since_id`
refresh. `status-tags.jsx` additionally renders a chip row for payload
tags that are not already visible in the content.

**Tuba.** `RichLabel.on_activate_link` (`RichLabel.vala:131-205`) first
matches mentions by exact URL, then parses the URL and checks
`Path.get_basename (Path.get_dirname (url)) == "tags"`; if so it opens
`new Views.Hashtag (Path.get_basename (uri.get_path ()))`. Using
`GLib.Uri` means query parameters are tolerated. Everything else goes
through aggressive resolving or the in-app WebKit browser/system
browser. `Views.Hashtag` is a normal `Views.Timeline` over
`/api/v1/timelines/tag/<tag>` with a Follow/Unfollow button, a
Feature-on-Profile button (Mastodon 5+/featured tags), and a streaming
subscription (`stream=hashtag&tag=…`). Separately, `MarkupView` runs
`TagExtractor` over the last paragraph: when that paragraph is nothing
but hashtag links, it is removed from the body and rendered as a
`HashtagBar` of at most three chips plus “Show N More”
(`HashtagBar.vala`).

**Babble.** `HashtagPage` already does more than either reference on the
page side (pin, stats, multi-tag definitions). What is missing is a
reliable decision at the click, and one routing path for the surfaces
that bypass `open_link`.

### 1.3 What each does best

- **From Phanpy:** decide from the anchor's `class` and use the display
  text as the tag name. This is the only rule that survives normalized
  `tags` payloads and servers that do not use `/tags/`.
- **From Tuba:** keep the URL-shape rule as a fallback for anchors that
  omit the class, and route every tag-shaped link in-app instead of
  asking the payload for permission.
- **Already better in Babble:** the destination page itself (pin,
  stats, saved multi-tag timelines) and one link handler shared by mouse
  and keyboard (Tab/Enter over links in `InlineEmojiText`).
- **Neither reference has:** a single handler for every post surface;
  Babble needs one because it renders post text in four places
  (timeline/thread, notifications, announcements, edit history).

## 2. Plan

### 2.1 Decision

Use **Phanpy's signal as primary and Tuba's shape as fallback**, and
delete the payload guard:

1. If the anchor has `class="hashtag"`, the click is a hashtag, and the
   tag name is the link's display text without `#`.
2. Otherwise, if the URL path is `/tags/<name>` or `/tag/<name>`, it is
   a hashtag; use the path segment.
3. Otherwise it is an ordinary link and keeps today's routing
   (mentions, Collections, aggressive resolving, web column/browser).

The `status.tags` payload stops being a gate. It is still useful for
other things (provenance, Catch Up), just not for deciding whether a
clicked anchor is a hashtag.

### 2.2 Changes by file

| File | Change |
|---|---|
| `src/statustext.py` | `_ContentParser` records whether the open `<a>` has the `hashtag` class; `_flush` emits `hashtag: True` on that segment. `parse_content` preserves the flag through whitespace normalization and the same-href merge. |
| `src/textemoji.py` | `set_segments` builds `self._link_hashtags = {href: text_without_#}` from flagged segments (cleared on every bind, like `_links`). `_open` passes `tag=self._link_hashtags.get(href)` to `open_link`. |
| `src/pages.py` | `open_link(widget, uri, mentions=None, tag=None, resolve=False)`: after the `is_web_link`/client checks, `if tag: push_hashtag(widget, client, tag); return`. Drop the `tags` parameter and the `known` set. Keep the `_TAG_PATH` branch as the fallback, now unconditional. |
| `src/statusrow.py` | Drop `self.content.link_tags = status.get('tags')` (line 601). Keep `link_mentions`. |
| `src/announcements.py` | Drop `link_tags` (line 234). |
| `src/profile.py` | `link_context` returns mentions only; `_rich_text` stops assigning `link_tags`. Hashtag anchors in bios carry the class, so they still route. |
| `src/notifications.py` | `NotificationPost` connects `activate-link`; the handler routes through `pages.open_link` with the tag map built in `_show`, and returns `True` so GTK never opens the URI itself. |
| `src/hashtags.py` | No change. `push_hashtag`/`HashtagPage` are reused as they are. |

Sketch of the parser change:

```python
if tag == 'a':
    self._flush()
    attrs = dict(attrs)
    self._href = attrs.get('href')
    classes = (attrs.get('class') or '').split()
    self._hashtag = 'hashtag' in classes
...
def _flush(self):
    ...
    segment = {'text': text, 'href': self._href}
    if self._hashtag:
        segment['hashtag'] = True
    self.segments.append(segment)
```

Sketch of the click path:

```python
def _open(self, href):
    from .pages import open_link
    open_link(self, href,
              mentions=getattr(self, 'link_mentions', None),
              tag=self._link_hashtags.get(href))
```

### 2.3 Behaviour after

- Clicking or keyboard-activating `#tag` in any post opens
  `HashtagPage` for that tag, whatever the tag's spelling, host, or
  server software.
- The same holds in notification post previews, announcements, edit
  history, profile bios and search rows.
- Ordinary links are unchanged; `open-links-in-app` and
  `aggressive-resolving` still decide where they go.
- No new translatable strings.

### 2.4 Tests

- **`src/test-statustext.py`**: a `class="hashtag"` anchor is flagged and
  its text kept; a plain `<a>` is not; an accented href
  (`/tags/K%C3%BCnstlicheIntelligenz`) and a non-`/tags/` href
  (`/dashboard/search/photo%20friday`) are both flagged; the flag
  survives the same-href merge.
- **New `src/test-hashtag-links.py`** (standalone, registered in
  `src/meson.build`):
  - the body maps an accented href to its displayed tag and
    `open_link` with that `tag` calls `hashtags.push_hashtag`,
    regardless of the payload's spelling.
  - `open_link` on `/tags/Ad%C3%A1s` with no `tag` still routes via the
    shape fallback.
  - `open_link` on `/dashboard/search/photo%20friday` with
    `tag='photo-friday'` routes; without `tag` it goes to `open_web`.
  - a non-tag link still reaches `open_web`/`launch_external`.
  - `NotificationPost` emits `activate-link`, handles it itself
    (`True`), and routes a hashtag; an ordinary link still reaches
    `open_web`.
- Run under the headless-mutter line in `AGENTS.md`, then
  `ruff check src/`.

### 2.5 Out of scope, recorded for later

- **Streaming the open tag timeline** (Tuba does it; Babble's
  `streaming.py` already has `hashtag_stream`, but `HashtagPage` never
  subscribes).
- **A media-only filter** and inline add/remove of tags on the page
  (Phanpy).
- **A trailing-tag chip bar** (Tuba) — only useful when authors put tags
  in a final tags-only paragraph; the content links already cover the
  rest.
- **`catchup.digest.extract_hashtags`** can now read the parser's flag
  instead of matching `class="hashtag"` itself. Separate cleanup; it
  deliberately does not use `parse_content` today.

### 2.6 Verification

- `ruff check src/` — clean.
- `meson test -C build 'hashtag links' 'status text' 'notifications
  render' 'timeline memory'` under the headless-mutter line in
  `AGENTS.md` — 4 passed, 0 failed.
- Full suite under the same line — 70 passed, 1 skipped (`streaming
  helpers`, which needs a secret service), 0 failed.
- Live payloads from `mastodon.social`: `117530045090267153` (payload
  `adas`, link `#Adás`) and `117530199006753870` (Wafrn
  `/dashboard/search/photo%20friday`) are both flagged with their
  displayed names.
