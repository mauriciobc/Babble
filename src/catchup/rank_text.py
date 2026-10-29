# SPDX-License-Identifier: GPL-3.0-or-later
"""Text parsing, Unicode parity and embeddings for the Catch Up ranker."""

import datetime
import functools
import math
import re
import struct
import threading
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, NamedTuple

EPSILON = 1e-9
# region JavaScript parity helpers

# What JavaScript's `\s` and `String.prototype.trim` treat as whitespace.
# Python's `str.strip()` and `\s` differ (they add U+001C-U+001F and U+0085
# and lack U+FEFF), and these sets decide text lengths and URL spans.
_JS_WHITESPACE = ''.join(chr(c) for c in (
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680,
    *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF))
_WS_CLASS = re.escape(_JS_WHITESPACE)


def _js_length(text):
    """``text.length`` in JavaScript: UTF-16 code units, not code points."""
    return len(text.encode('utf-16-le', 'surrogatepass')) // 2


def _utf16_key(text):
    """Sort key matching JavaScript's ``<`` on strings (UTF-16 code units)."""
    return text.encode('utf-16-be', 'surrogatepass')


def _js_slice(text, start, end=None):
    """``text.slice(start, end)`` over UTF-16 code units."""
    raw = text.encode('utf-16-le', 'surrogatepass')
    units = len(raw) // 2
    if end is None:
        end = units
    if start < 0:
        start = max(0, units + start)
    if end < 0:
        end = max(0, units + end)
    return raw[2 * start:2 * min(end, units)].decode(
        'utf-16-le', 'surrogatepass')


_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
_DATETIME = datetime.datetime


def parse_timestamp_ms(value):
    """``Date.parse`` for the ISO-8601 stamps Mastodon sends.

    Returns epoch milliseconds, or ``None`` where JavaScript yields ``NaN``.
    A stamp without an offset is read as UTC.

    Deliberately *not* statustext.parse_time, despite the two agreeing on
    every input: this one is pinned to the original JavaScript by
    test-catchup-rank.py's golden scores, and a change here that altered
    a score would mean the port had drifted rather than been fixed. It
    keeps its own reader, and the docstring is why.
    """
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    delta = moment - _EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1000
            + delta.microseconds // 1000)


def _to_ms(moment):
    """Epoch milliseconds from the ``now`` a caller passes in."""
    if isinstance(moment, _DATETIME):
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        delta = moment - _EPOCH
        return ((delta.days * 86400 + delta.seconds) * 1000
                + delta.microseconds // 1000)
    ms = parse_timestamp_ms(moment)
    if ms is None:
        raise ValueError('now must be a datetime or an ISO-8601 string, '
                         'got %r' % (moment,))
    return ms


def _count(mapping, key):
    """``mapping.key ?? 0`` for a numeric field."""
    value = mapping.get(key)
    return 0 if value is None else value


# endregion
# region html to text

_VOID_ELEMENTS = frozenset((
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr'))


class _Node:
    """A node of the little tree ``htmlToText`` walks (ultrahtml's)."""

    __slots__ = ('name', 'attrs', 'children', 'parent', 'value')

    def __init__(self, name=None, attrs=None, parent=None, value=None):
        self.name = name          # None for a text node
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.value = value


class _TreeBuilder(HTMLParser):
    def __init__(self):
        # convert_charrefs: text arrives decoded, which is what ultrahtml's
        # `decode(input.value)` does in treeToText.
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {k: ('' if v is None else v) for k, v in attrs},
                     self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(node)

    def handle_endtag(self, tag):
        if tag in _VOID_ELEMENTS:
            return
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].name == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data):
        self._stack[-1].children.append(
            _Node(parent=self._stack[-1], value=data))


_EMOJI_ID_NEEDS_WRAPPING_RE = re.compile(r'^[A-Za-z0-9_-]+\Z')


# treeToText in content-parse.ts.
def _tree_to_text(node):
    if node.name is None:
        return node.value
    if node.name == 'br':
        return '\n'

    pre = ''
    post = ''
    if node.name in ('p', 'pre'):
        pre = '\n'

    if node.attrs.get('data-type') == 'mention':
        acct = node.attrs.get('data-id')
        if acct:
            return acct if acct.startswith('@') else '@' + acct

    if node.name == 'code':
        if node.parent is not None and node.parent.name == 'pre':
            lang = node.attrs.get('class')
            if lang is not None:
                lang = lang.replace('language-', '', 1)
            pre = '```%s\n' % (lang or '')
            post = '\n```'
        else:
            pre = '`'
            post = '`'
    elif node.name in ('b', 'strong'):
        pre = post = '**'
    elif node.name in ('i', 'em'):
        pre = post = '*'
    elif node.name == 'del':
        pre = post = '~~'

    body = ''.join(_tree_to_text(child) for child in node.children)

    if node.name in ('img', 'picture'):
        css = node.attrs.get('class')
        if css is not None and 'custom-emoji' in css:
            emoji_id = next(
                (node.attrs[k] for k in ('data-emoji-id', 'alt', 'title')
                 if k in node.attrs), 'unknown')
            if _EMOJI_ID_NEEDS_WRAPPING_RE.match(emoji_id):
                return ':%s:' % emoji_id
            return emoji_id
        if css is not None and 'iconify-emoji' in css:
            return node.attrs.get('alt', '')

    return pre + body + post


# htmlToText in content-parse.ts: status HTML to plain text, with the
# markdown-ish marks Elk's version adds for code, bold, italic and strike.
def html_to_text(html):
    # Elk wraps the parse and the walk in try/catch and returns '' on
    # failure. The walk recurses once per nesting level, so deeply nested
    # status HTML raises RecursionError here; one pathological post must
    # not abort the ranking of the whole snapshot.
    try:
        builder = _TreeBuilder()
        builder.feed(html)
        builder.close()
        text = ''.join(_tree_to_text(child) for child in builder.root.children)
    except (RecursionError, ValueError):
        return ''
    return text.strip(_JS_WHITESPACE)


# endregion

# region Unicode tables
_EXTENDED_PICTOGRAPHIC = (
    (0xA9, 0xA9), (0xAE, 0xAE), (0x203C, 0x203C), (0x2049, 0x2049),
    (0x2122, 0x2122), (0x2139, 0x2139), (0x2194, 0x2199), (0x21A9, 0x21AA),
    (0x231A, 0x231B), (0x2328, 0x2328), (0x2388, 0x2388), (0x23CF, 0x23CF),
    (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x24C2, 0x24C2), (0x25AA, 0x25AB),
    (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE), (0x2600, 0x2605),
    (0x2607, 0x2612), (0x2614, 0x2685), (0x2690, 0x2705), (0x2708, 0x2712),
    (0x2714, 0x2714), (0x2716, 0x2716), (0x271D, 0x271D), (0x2721, 0x2721),
    (0x2728, 0x2728), (0x2733, 0x2734), (0x2744, 0x2744), (0x2747, 0x2747),
    (0x274C, 0x274C), (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757),
    (0x2763, 0x2767), (0x2795, 0x2797), (0x27A1, 0x27A1), (0x27B0, 0x27B0),
    (0x27BF, 0x27BF), (0x2934, 0x2935), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50), (0x2B55, 0x2B55), (0x3030, 0x3030), (0x303D, 0x303D),
    (0x3297, 0x3297), (0x3299, 0x3299), (0x1F000, 0x1F0FF),
    (0x1F10D, 0x1F10F), (0x1F12F, 0x1F12F), (0x1F16C, 0x1F171),
    (0x1F17E, 0x1F17F), (0x1F18E, 0x1F18E), (0x1F191, 0x1F19A),
    (0x1F1AD, 0x1F1E5), (0x1F201, 0x1F20F), (0x1F21A, 0x1F21A),
    (0x1F22F, 0x1F22F), (0x1F232, 0x1F23A), (0x1F23C, 0x1F23F),
    (0x1F249, 0x1F3FA), (0x1F400, 0x1F53D), (0x1F546, 0x1F64F),
    (0x1F680, 0x1F6FF), (0x1F774, 0x1F77F), (0x1F7D5, 0x1F7FF),
    (0x1F80C, 0x1F80F), (0x1F848, 0x1F84F), (0x1F85A, 0x1F85F),
    (0x1F888, 0x1F88F), (0x1F8AE, 0x1F8FF), (0x1F90C, 0x1F93A),
    (0x1F93C, 0x1F945), (0x1F947, 0x1FAFF), (0x1FC00, 0x1FFFD),
)

_HAN = (
    (0x2E80, 0x2E99), (0x2E9B, 0x2EF3), (0x2F00, 0x2FD5), (0x3005, 0x3005),
    (0x3007, 0x3007), (0x3021, 0x3029), (0x3038, 0x303B), (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF), (0xF900, 0xFA6D), (0xFA70, 0xFAD9), (0x16FE2, 0x16FE3),
    (0x16FF0, 0x16FF1), (0x20000, 0x2A6DF), (0x2A700, 0x2B739),
    (0x2B740, 0x2B81D), (0x2B820, 0x2CEA1), (0x2CEB0, 0x2EBE0),
    (0x2EBF0, 0x2EE5D), (0x2F800, 0x2FA1D), (0x30000, 0x3134A),
    (0x31350, 0x323AF),
)

_HIRAGANA = (
    (0x3041, 0x3096), (0x309D, 0x309F), (0x1B001, 0x1B11F),
    (0x1B132, 0x1B132), (0x1B150, 0x1B152), (0x1F200, 0x1F200),
)

_KATAKANA = (
    (0x30A1, 0x30FA), (0x30FD, 0x30FF), (0x31F0, 0x31FF), (0x32D0, 0x32FE),
    (0x3300, 0x3357), (0xFF66, 0xFF6F), (0xFF71, 0xFF9D), (0x1AFF0, 0x1AFF3),
    (0x1AFF5, 0x1AFFB), (0x1AFFD, 0x1AFFE), (0x1B000, 0x1B000),
    (0x1B120, 0x1B122), (0x1B155, 0x1B155), (0x1B164, 0x1B167),
)

_THAI = (
    (0xE01, 0xE3A), (0xE40, 0xE5B),
)

_KHMER = (
    (0x1780, 0x17DD), (0x17E0, 0x17E9), (0x17F0, 0x17F9), (0x19E0, 0x19FF),
)

_LAO = (
    (0xE81, 0xE82), (0xE84, 0xE84), (0xE86, 0xE8A), (0xE8C, 0xEA3),
    (0xEA5, 0xEA5), (0xEA7, 0xEBD), (0xEC0, 0xEC4), (0xEC6, 0xEC6),
    (0xEC8, 0xECE), (0xED0, 0xED9), (0xEDC, 0xEDF),
)

_MYANMAR = (
    (0x1000, 0x109F), (0xA9E0, 0xA9FE), (0xAA60, 0xAA7F), (0x116D0, 0x116E3),
)

_HANGUL = (
    (0x1100, 0x11FF), (0x302E, 0x302F), (0x3131, 0x318E), (0x3200, 0x321E),
    (0x3260, 0x327E), (0xA960, 0xA97C), (0xAC00, 0xD7A3), (0xD7B0, 0xD7C6),
    (0xD7CB, 0xD7FB), (0xFFA0, 0xFFBE), (0xFFC2, 0xFFC7), (0xFFCA, 0xFFCF),
    (0xFFD2, 0xFFD7), (0xFFDA, 0xFFDC),
)
# endregion
# region diversity constants

# EMBEDDING_DIM in diversity.ts: the hashed feature space.
EMBEDDING_DIM = 4096
# DEFAULT_DIVERSITY_THETA: vm-ranker's dpp_theta.
DEFAULT_DIVERSITY_THETA = 0.5
# DEFAULT_DIVERSITY_LIMIT: vm-ranker's dpp_top_k.
DEFAULT_DIVERSITY_LIMIT = 50
# DEFAULT_DIVERSITY_POOL_SIZE: max_selected_rank, 200 against a top_k of 50.
DEFAULT_DIVERSITY_POOL_SIZE = 200
# DEFAULT_DIVERSITY_LAMBDA: for the linear penalty modes only.
DEFAULT_DIVERSITY_LAMBDA = 0.25
# DEFAULT_MAX_PER_CONVERSATION: how many branches of a thread survive.
DEFAULT_MAX_PER_CONVERSATION = 3

# MIN_RESIDUAL: vm-ranker's EPSILON, the conditional variance floor.
_MIN_RESIDUAL = 1e-6

# Relative pull of each feature family on the cosine.
_WEIGHT_AUTHOR = 0.4
_WEIGHT_TAGS = 1.2
_WEIGHT_TOKENS = 1.2
_WEIGHT_LINK = 0.8
_WEIGHT_EMOJI = 0.4
_WEIGHT_LANGUAGE = 0.35
_WEIGHT_SPOILER = 0.3
_WEIGHT_MEDIA = 0.3

# Within the link family: which story, mostly, and which outlet, a little.
_LINK_PATH_SHARE = 0.75
_LINK_HOST_SHARE = 0.25

# Hard bound on how much text is tokenised; sampled head and tail.
_MAX_TEXT_LENGTH = 4000
# Distinct tokens retained before the per-post selection step.
_MAX_DISTINCT_TOKENS = 512
# Distinct tokens that actually reach the embedding.
_MAX_TOKENS = 48

# endregion

# region tokenizer


def _ranges_class(ranges):
    """The body of a regex character class for (first, last) code points."""
    return ''.join('\\U%08x-\\U%08x' % pair for pair in ranges)


def _category_ranges(prefixes):
    """Code point ranges whose general category starts with a prefix."""
    ranges = []
    start = None
    category = unicodedata.category
    for code in range(0x110000):
        hit = category(chr(code))[0] in prefixes
        if hit and start is None:
            start = code
        elif not hit and start is not None:
            ranges.append((start, code - 1))
            start = None
    if start is not None:
        ranges.append((start, 0x10FFFF))
    return ranges


class _Patterns(NamedTuple):
    hashtag: Any
    emoji: Any
    token_split: Any
    needs_segmentation: Any
    single_char_ok: Any


_PATTERNS = None
_PATTERNS_LOCK = threading.Lock()


def _patterns():
    """The Unicode-property regexes, built once and shared by every thread.

    ``functools.lru_cache`` is thread-safe but not single-flight: two threads
    arriving together would each run the 100 ms scan. The build is guarded
    instead, so a second caller waits for the first one's result.
    """
    global _PATTERNS
    if _PATTERNS is None:
        with _PATTERNS_LOCK:
            if _PATTERNS is None:
                _PATTERNS = _build_patterns()
    return _PATTERNS


def _build_patterns():
    """The Unicode-property regexes of diversity.ts, built on first use.

    Python's ``re`` has no ``\\p{...}``. General categories (``\\p{L}``,
    ``\\p{N}``, ``\\p{M}``) are scanned out of ``unicodedata`` once (about
    0.2 s), scripts and ``Extended_Pictographic`` come from the tables above.
    """
    word = _ranges_class(_category_ranges('LNM'))
    unspaced = ''.join(_ranges_class(table) for table in (
        _HAN, _HIRAGANA, _KATAKANA, _THAI, _KHMER, _LAO, _MYANMAR))
    single_ok = ''.join(_ranges_class(table) for table in (
        _HAN, _HIRAGANA, _KATAKANA, _HANGUL, _THAI))
    return _Patterns(
        # HASHTAG_RE = /#[\p{L}\p{N}\p{M}_]+/gu
        hashtag=re.compile('#[%s_]+' % word),
        # EMOJI_RE = /\p{Extended_Pictographic}/gu
        emoji=re.compile('[%s]' % _ranges_class(_EXTENDED_PICTOGRAPHIC)),
        # TOKEN_SPLIT_RE = /[^\p{L}\p{N}\p{M}_‌‍]+/u
        token_split=re.compile('[^%s_‌‍]+' % word),
        # NEEDS_SEGMENTATION_RE: scripts that do not delimit words.
        needs_segmentation=re.compile('[%s]' % unspaced),
        # SINGLE_CHAR_OK_RE: a character that is a morpheme by itself.
        single_char_ok=re.compile('[%s]' % single_ok),
    )


def prewarm():
    """Build the Unicode-property regexes ahead of the first ranking.

    The scan takes about 110 ms. Calling this on a worker thread while the
    reader opens Catch Up moves it off the first ``rank()`` call; a ranking
    that starts before it finishes waits for the build in progress instead of
    repeating it.
    """
    _patterns()


# URL_RE = /https?:\/\/\S+/g, and MENTION_RE (ASCII \w, as in JavaScript).
_URL_RE = re.compile('https?://[^%s]+' % _WS_CLASS)
_MENTION_RE = re.compile(r'@[\w.-]+(?:@[\w.-]+)?', re.ASCII)


# bigrams in diversity.ts: the CJK fallback when no segmenter exists.
def _bigrams(run, out):
    chars = list(run)
    if len(chars) == 1:
        out.append(chars[0])
        return
    for i in range(len(chars) - 1):
        out.append(chars[i] + chars[i + 1])


# keepToken in diversity.ts. Lengths are UTF-16 lengths, as in JavaScript.
def _keep_token(token):
    if len(token) == 0:
        return False
    if _js_length(token) >= 2:
        return True
    return _patterns().single_char_ok.match(token) is not None


# tokenizeText in diversity.ts: distinct, script-aware content tokens.
# NFC first, no stop-word list (IDF down-weights ubiquitous tokens).
def tokenize_text(text):
    if not text:
        return []
    patterns = _patterns()

    # Bound the work before doing anything quadratic-ish to it.
    bounded = text
    if _js_length(bounded) > _MAX_TEXT_LENGTH:
        half = _MAX_TEXT_LENGTH >> 1
        bounded = (_js_slice(bounded, 0, half) + ' '
                   + _js_slice(bounded, -half))

    cleaned = unicodedata.normalize('NFC', bounded).lower()
    cleaned = _URL_RE.sub(' ', cleaned)
    cleaned = _MENTION_RE.sub(' ', cleaned)
    cleaned = patterns.hashtag.sub(' ', cleaned)
    cleaned = patterns.emoji.sub(' ', cleaned)

    tokens = {}
    for raw in patterns.token_split.split(cleaned):
        if len(raw) == 0:
            continue

        if patterns.needs_segmentation.search(raw):
            # Intl.Segmenter is not available: take the bigram path.
            pieces = []
            _bigrams(raw, pieces)
            for piece in pieces:
                if _keep_token(piece):
                    tokens[piece] = None
        elif _keep_token(raw):
            tokens[raw] = None

        if len(tokens) >= _MAX_DISTINCT_TOKENS:
            break

    return list(tokens)


# endregion

# region hashing

_HASH_SEED_BUCKET = 0x811C9DC5
_HASH_SEED_SIGN = 0x9E3779B1


# hashString in diversity.ts: FNV-1a, 32-bit, over UTF-16 code units (what
# charCodeAt yields), so the buckets match Elk's. A pure function, and the
# keys repeat across a pool ('l:en', a shared author, common words, link
# hosts), so a bounded cache pays for itself inside one catch-up.
@functools.lru_cache(maxsize=16384)
def _hash_string(value, seed):
    h = seed & 0xFFFFFFFF
    raw = value.encode('utf-16-le', 'surrogatepass')
    for unit in struct.unpack('<%dH' % (len(raw) // 2), raw):
        h ^= unit
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


# endregion

# region feature extraction


# CandidateFeatures in diversity.ts: the content signals a client can
# extract. The *_ lists are ordered, de-duplicated (JavaScript Sets).
@dataclass
class CandidateFeatures:
    tokens: list = field(default_factory=list)
    spoiler_tokens: list = field(default_factory=list)
    hashtags: list = field(default_factory=list)
    emoji: list = field(default_factory=list)
    author_id: str = ''
    language: str | None = None
    media_types: list = field(default_factory=list)
    link_paths: list = field(default_factory=list)
    link_hosts: list = field(default_factory=list)
    # Stable fallback so a featureless post still gets a unit vector.
    fallback_key: str = ''


_HREF_RE = re.compile(r'href="([^"]+)"')
_TAG_PATH_RE = re.compile(r'/tags?/', re.IGNORECASE)
_PROFILE_PATH_RE = re.compile(r'/@[^/]+/?\Z')
_LINK_PARTS_RE = re.compile(r'^https?://([^/?#]+)([^?#]*)', re.IGNORECASE)
_WWW_PREFIX_RE = re.compile(r'^www\.')
_TRAILING_SLASH_RE = re.compile(r'/+\Z')


# linkParts in diversity.ts: host and host/path of an outbound link, or
# None for in-fediverse chrome (profile and tag pages).
def _link_parts(href):
    if _TAG_PATH_RE.search(href) or _PROFILE_PATH_RE.search(href):
        return None
    match = _LINK_PARTS_RE.match(href)
    if not match:
        return None
    host = _WWW_PREFIX_RE.sub('', match.group(1).lower())
    path = _TRAILING_SLASH_RE.sub('', match.group(2) or '')
    return host, host + path


# distinctEmoji in diversity.ts: at most 16 distinct emoji.
def _distinct_emoji(text):
    found = {}
    for match in _patterns().emoji.finditer(text):
        found[match.group(0)] = None
        if len(found) >= 16:
            break
    return list(found)


# extractCandidateFeatures in diversity.ts: the signals of the *original*
# post (a boost is judged by what it boosts).
def extract_candidate_features(status):
    reblog = status.get('reblog')
    post = status if reblog is None else reblog
    html = post.get('content') or ''
    body = html_to_text(html) if html else ''
    spoiler = post.get('spoiler_text') or ''

    hashtags = {}
    for tag in post.get('tags') or ():
        if tag and tag.get('name'):
            name = unicodedata.normalize('NFC', tag['name']).lower()
            hashtags[name] = None
    # Servers do not always populate `tags`, so fall back to what looks
    # like a hashtag in the rendered text.
    for raw in _patterns().hashtag.findall(
            unicodedata.normalize('NFC', body)):
        hashtags[raw[1:].lower()] = None

    media_types = {}
    for media in post.get('media_attachments') or ():
        if media and media.get('type'):
            media_types[media['type']] = None

    link_paths = {}
    link_hosts = {}

    def collect_link(href):
        parts = _link_parts(href)
        if parts is None:
            return
        link_hosts[parts[0]] = None
        link_paths[parts[1]] = None

    for match in _HREF_RE.finditer(html):
        collect_link(match.group(1))
    card = post.get('card')
    if card and card.get('url'):
        collect_link(card['url'])

    account = post.get('account') or {}
    return CandidateFeatures(
        # The content warning is part of the post's topic, so its tokens
        # go in the body bag and get their own small family too.
        tokens=tokenize_text(spoiler + '\n' + body if spoiler else body),
        spoiler_tokens=tokenize_text(spoiler),
        hashtags=list(hashtags),
        emoji=_distinct_emoji(body),
        author_id=account.get('id') or '',
        language=post.get('language'),
        media_types=list(media_types),
        link_paths=list(link_paths),
        link_hosts=list(link_hosts),
        fallback_key=post.get('id') or '',
    )


# endregion

# region embedding


# SparseVector in diversity.ts: parallel index and value lists.
class SparseVector(NamedTuple):
    indices: list
    values: list


# computeTokenIdf in diversity.ts: log(1 + n / (1 + df)).
def compute_token_idf(features):
    df = {}
    for f in features:
        for token in f.tokens:
            df[token] = df.get(token, 0) + 1
    n = len(features)
    return {token: math.log(1 + n / (1 + count))
            for token, count in df.items()}


# selectTokens in diversity.ts: trim a token bag to MAX_TOKENS. With a
# corpus keep the most distinctive; without, sample at an even stride.
def _select_tokens(tokens, idf=None):
    if len(tokens) <= _MAX_TOKENS:
        return tokens
    if idf is not None:
        return sorted(tokens, key=lambda t: (-idf.get(t, 0), _utf16_key(t))
                      )[:_MAX_TOKENS]
    stride = len(tokens) / _MAX_TOKENS
    return [tokens[math.floor(i * stride)] for i in range(_MAX_TOKENS)]


# sparseEmbed in diversity.ts: hash a post's features into a sparse unit
# vector (hashing trick with sign hashing).
def sparse_embed(features, idf=None):
    acc = {}

    def add(key, weight):
        if weight == 0:
            return
        bucket = _hash_string(key, _HASH_SEED_BUCKET) % EMBEDDING_DIM
        sign = 1 if (_hash_string(key, _HASH_SEED_SIGN) & 1) == 0 else -1
        acc[bucket] = acc.get(bucket, 0) + sign * weight

    def add_family(prefix, keys, family_weight, weights=None):
        """L2-normalise a family internally, then scale to its weight."""
        if len(keys) == 0 or family_weight == 0:
            return
        norm = 0
        for i in range(len(keys)):
            w = weights[i] if weights else 1
            norm += w * w
        if norm <= EPSILON:
            return
        scale = family_weight / math.sqrt(norm)
        for i in range(len(keys)):
            add(prefix + keys[i], (weights[i] if weights else 1) * scale)

    if features.author_id:
        add('a:' + features.author_id, _WEIGHT_AUTHOR)
    if features.language:
        add('l:' + features.language.lower(), _WEIGHT_LANGUAGE)

    add_family('h:', features.hashtags, _WEIGHT_TAGS)
    add_family('m:', features.media_types, _WEIGHT_MEDIA)
    add_family('e:', features.emoji, _WEIGHT_EMOJI)
    add_family('c:', _select_tokens(features.spoiler_tokens, idf),
               _WEIGHT_SPOILER)
    add_family('u:', features.link_paths, _WEIGHT_LINK * _LINK_PATH_SHARE)
    add_family('d:', features.link_hosts, _WEIGHT_LINK * _LINK_HOST_SHARE)

    tokens = _select_tokens(features.tokens, idf)
    add_family('t:', tokens, _WEIGHT_TOKENS,
               [idf.get(t, 1) for t in tokens] if idf is not None else None)

    if len(acc) == 0:
        # vm-ranker hands a random unit vector to candidates whose
        # embedding is missing. Same idea, seeded off the post id so the
        # order is the same on every load.
        seed = features.fallback_key or 'empty'
        for i in range(4):
            add('z:%d:%s' % (i, seed), 1)

    norm = 0
    for value in acc.values():
        norm += value * value
    inv = 1 / math.sqrt(norm) if norm > EPSILON else 0

    return SparseVector(list(acc), [value * inv for value in acc.values()])


# densify in diversity.ts.
def _densify(sparse):
    dense = [0.0] * EMBEDDING_DIM
    for index, value in zip(sparse.indices, sparse.values, strict=True):
        dense[index] = value
    return dense


# embedFeatures in diversity.ts: features to a dense unit-norm vector.
def embed_features(features, idf=None):
    return _densify(sparse_embed(features, idf))


# embedCandidate in diversity.ts: the corpus-free embedding of one post.
def embed_candidate(candidate):
    return embed_features(extract_candidate_features(candidate.status))


# embedCandidates in diversity.ts: dense embeddings for a whole set, with
# IDF measured over that set.
def embed_candidates(candidates):
    return [_densify(v) for v in embed_candidates_sparse(candidates)]


def embed_candidates_sparse(candidates):
    """``embed_candidates`` without the 4096-wide densify (Python only)."""
    features = [extract_candidate_features(c.status) for c in candidates]
    idf = compute_token_idf(features)
    return [sparse_embed(f, idf) for f in features]


# cosineSimilarity in diversity.ts: in [-1, 1], 0 if either is degenerate.
def cosine_similarity(a, b):
    dot = 0
    na = 0
    nb = 0
    for x, y in zip(a, b, strict=False):  # min(len), as in Elk
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= EPSILON:
        return 0
    return dot / denom


def sparse_cosine_similarity(a, b):
    """``cosine_similarity`` over two SparseVectors (Python only)."""
    other = dict(zip(b.indices, b.values, strict=True))
    dot = 0
    for index, value in zip(a.indices, a.values, strict=True):
        y = other.get(index)
        if y is not None:
            dot += value * y
    na = sum(v * v for v in a.values)
    nb = sum(v * v for v in b.values)
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= EPSILON:
        return 0
    return dot / denom


# endregion
