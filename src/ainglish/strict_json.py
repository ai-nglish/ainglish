"""strict-json-v1: refuse a JSON text that a decoder would change in silence, before it is decoded.

Pure functions. No network, no file, no clock. Nothing here is wired into a client call, a manifest,
a mint or a filing: this module only states the rule and computes it, so that the register and the
SDK can be shown to agree on it, over one shared vector file, before a record depends on it.

Why. Both Python's `json.loads` and PHP's `json_decode` keep the LAST of two members with the same
name and say nothing. A client that hashes what it decoded and a server that hashes what it decoded
then agree on a digest of an object neither of them was sent. A digest check is a transport check;
it cannot see a duplicate. The only place a duplicate is visible is the raw text.

The rule, on the raw bytes, in this order:

1. size       more than `max_bytes` bytes. Nothing else is looked at.
2. utf8       the bytes are not well-formed UTF-8 (overlong forms, encoded surrogates, values above
              U+10FFFF and cut-off sequences are all ill-formed). The whole input is checked first.
3. then one pass from left to right, which stops at the FIRST of:
   syntax         the text is not one JSON value by RFC 8259. A byte-order mark, a comment, a
                  trailing comma, NaN, Infinity, a single-quoted string, a raw control character in
                  a string and data after the value are all syntax faults.
   surrogate      a \\uD800..\\uDFFF escape that is not a high surrogate followed at once by a low one.
   depth          an array or object opens at a nesting level above `max_depth`.
   duplicate_key  an object holds two members whose names are equal AFTER escape decoding, at any
                  nesting level. "a" and "\\u0061" are one name. So are "\\/" and "/", and a
                  surrogate-pair escape and the character it spells.

Names are compared as decoded code points, exactly. No Unicode normalisation and no case folding is
applied: a precomposed e-acute and an e followed by a combining acute are two names here, as they are
to both decoders, so neither is lost. "1", "01" and "1.0" are three names.

`offset` in a refusal is a byte offset into the raw input: `max_bytes` for size; the first byte of
the ill-formed sequence for utf8; the opening bracket for depth; the opening quote of the second
name for duplicate_key; the backslash of the offending escape for surrogate and for a malformed
escape; and for other syntax faults the byte where the text stops being JSON (the input length when
it ends too early).

What this does not establish. It does not say the value has the right shape, that a number fits any
type, or that a name is safe to store. It reads the grammar only. Shape, ranges and name rules belong
to whatever reads the decoded value afterwards.
"""
from __future__ import annotations

RULE = "strict-json-v1"
CODES = ("size", "utf8", "syntax", "surrogate", "depth", "duplicate_key")
DEPTH_CEILING = 64          # the scan recurses once per level; no caller may ask for more
BYTES_CEILING = 16 * 1024 * 1024

_WS = (0x20, 0x09, 0x0A, 0x0D)
_SIMPLE = {0x22: 0x22, 0x5C: 0x5C, 0x2F: 0x2F, 0x62: 0x08, 0x66: 0x0C, 0x6E: 0x0A, 0x72: 0x0D, 0x74: 0x09}
_HEX = {c: v for v, c in enumerate(b"0123456789abcdef")}
_HEX.update({c: v + 10 for v, c in enumerate(b"ABCDEF")})
_LITERALS = (b"true", b"false", b"null")


class StrictJsonRefused(ValueError):
    """A raw text the rule does not admit. `code` is one of CODES; `offset` is a byte offset."""

    def __init__(self, code, offset, message):
        super().__init__(message)
        self.code = code
        self.offset = offset


def utf8_fault(raw):
    """The offset of the first byte of the first ill-formed UTF-8 sequence in `raw`, or -1."""
    if raw.isascii():
        return -1
    i, n = 0, len(raw)
    while i < n:
        b = raw[i]
        if b < 0x80:
            i += 1
            continue
        if 0xC2 <= b <= 0xDF:
            need, lo, hi = 1, 0x80, 0xBF
        elif b == 0xE0:
            need, lo, hi = 2, 0xA0, 0xBF
        elif b == 0xED:
            need, lo, hi = 2, 0x80, 0x9F
        elif 0xE1 <= b <= 0xEF:
            need, lo, hi = 2, 0x80, 0xBF
        elif b == 0xF0:
            need, lo, hi = 3, 0x90, 0xBF
        elif 0xF1 <= b <= 0xF3:
            need, lo, hi = 3, 0x80, 0xBF
        elif b == 0xF4:
            need, lo, hi = 3, 0x80, 0x8F
        else:
            return i
        for k in range(1, need + 1):
            if i + k >= n:
                return i
            c = raw[i + k]
            if not ((lo <= c <= hi) if k == 1 else (0x80 <= c <= 0xBF)):
                return i
        i += need + 1
    return -1


def _encode(cp, out):
    if cp < 0x80:
        out.append(cp)
    elif cp < 0x800:
        out.append(0xC0 | (cp >> 6)); out.append(0x80 | (cp & 0x3F))
    elif cp < 0x10000:
        out.append(0xE0 | (cp >> 12)); out.append(0x80 | ((cp >> 6) & 0x3F)); out.append(0x80 | (cp & 0x3F))
    else:
        out.append(0xF0 | (cp >> 18)); out.append(0x80 | ((cp >> 12) & 0x3F)); out.append(0x80 | ((cp >> 6) & 0x3F)); out.append(0x80 | (cp & 0x3F))


class _Scan:
    def __init__(self, raw, max_depth):
        self.b = raw
        self.n = len(raw)
        self.i = 0
        self.max_depth = max_depth
        self.depth = 0
        self.objects = 0
        self.members = 0

    def ws(self):
        while self.i < self.n and self.b[self.i] in _WS:
            self.i += 1

    def at(self, byte):
        return self.i < self.n and self.b[self.i] == byte

    def value(self, level):
        self.ws()
        if self.i >= self.n:
            raise StrictJsonRefused("syntax", self.n, "The text ends where a value is expected.")
        c = self.b[self.i]
        if c == 0x7B:
            return self.container(level + 1, True)
        if c == 0x5B:
            return self.container(level + 1, False)
        if c == 0x22:
            self.string()
            return "string"
        if c == 0x2D or 0x30 <= c <= 0x39:
            self.number()
            return "number"
        for word in _LITERALS:
            if self.b.startswith(word, self.i):
                self.i += len(word)
                return "literal"
        raise StrictJsonRefused("syntax", self.i, "This is not the start of a JSON value.")

    def container(self, level, is_object):
        if level > self.max_depth:
            raise StrictJsonRefused("depth", self.i, "The text nests deeper than %d." % self.max_depth)
        self.depth = max(self.depth, level)
        close = 0x7D if is_object else 0x5D
        seen = set()
        if is_object:
            self.objects += 1
        self.i += 1
        self.ws()
        if self.at(close):
            self.i += 1
            return "object" if is_object else "array"
        while True:
            if is_object:
                self.ws()
                if not self.at(0x22):
                    raise StrictJsonRefused("syntax", self.i, "An object member must start with a string.")
                start = self.i
                name = self.string()
                if name in seen:
                    raise StrictJsonRefused("duplicate_key", start, "An object holds the same member name twice.")
                seen.add(name)
                self.members += 1
                self.ws()
                if not self.at(0x3A):
                    raise StrictJsonRefused("syntax", self.i, "A colon must follow a member name.")
                self.i += 1
            self.value(level)
            self.ws()
            if self.at(0x2C):
                self.i += 1
                continue
            if self.at(close):
                self.i += 1
                return "object" if is_object else "array"
            raise StrictJsonRefused("syntax", self.i, "A comma or a closing bracket is expected.")

    def hex4(self, pos, escape_at):
        if pos + 4 > self.n:
            raise StrictJsonRefused("syntax", escape_at, "A \\u escape needs four hexadecimal digits.")
        cp = 0
        for k in range(4):
            v = _HEX.get(self.b[pos + k])
            if v is None:
                raise StrictJsonRefused("syntax", escape_at, "A \\u escape needs four hexadecimal digits.")
            cp = cp * 16 + v
        return cp

    def string(self):
        """At an opening quote. Returns the decoded content as UTF-8 bytes and moves past the closing quote."""
        self.i += 1
        out = bytearray()
        while True:
            if self.i >= self.n:
                raise StrictJsonRefused("syntax", self.n, "A string is not closed.")
            c = self.b[self.i]
            if c == 0x22:
                self.i += 1
                return bytes(out)
            if c < 0x20:
                raise StrictJsonRefused("syntax", self.i, "A raw control character stands inside a string.")
            if c != 0x5C:
                out.append(c)
                self.i += 1
                continue
            escape_at = self.i
            if self.i + 1 >= self.n:
                raise StrictJsonRefused("syntax", escape_at, "An escape is cut off.")
            e = self.b[self.i + 1]
            if e in _SIMPLE:
                out.append(_SIMPLE[e])
                self.i += 2
                continue
            if e != 0x75:
                raise StrictJsonRefused("syntax", escape_at, "This escape is not one JSON defines.")
            cp = self.hex4(self.i + 2, escape_at)
            self.i += 6
            if 0xDC00 <= cp <= 0xDFFF:
                raise StrictJsonRefused("surrogate", escape_at, "A low surrogate escape stands alone.")
            if 0xD800 <= cp <= 0xDBFF:
                if not (self.i + 1 < self.n and self.b[self.i] == 0x5C and self.b[self.i + 1] == 0x75):
                    raise StrictJsonRefused("surrogate", escape_at, "A high surrogate escape is not followed by a low one.")
                low = self.hex4(self.i + 2, self.i)
                if not 0xDC00 <= low <= 0xDFFF:
                    raise StrictJsonRefused("surrogate", escape_at, "A high surrogate escape is not followed by a low one.")
                self.i += 6
                cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00)
            _encode(cp, out)

    def digits(self):
        start = self.i
        while self.i < self.n and 0x30 <= self.b[self.i] <= 0x39:
            self.i += 1
        if self.i == start:
            raise StrictJsonRefused("syntax", self.i, "A digit is expected.")

    def number(self):
        if self.at(0x2D):
            self.i += 1
        if self.at(0x30):
            self.i += 1
        else:
            self.digits()
        if self.at(0x2E):
            self.i += 1
            self.digits()
        if self.at(0x65) or self.at(0x45):
            self.i += 1
            if self.at(0x2B) or self.at(0x2D):
                self.i += 1
            self.digits()


def _bound(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("%s must be an integer from %d to %d." % (name, low, high))


def scan(raw, *, max_bytes, max_depth):
    """Check `raw` (bytes) against strict-json-v1. Returns {"top", "depth", "objects", "members"}:
    the kind of the top-level value (object, array, string, number or literal), the deepest nesting
    level reached (0 for a bare scalar), and how many objects and object members the text holds.
    Raises StrictJsonRefused with a code and a byte offset. Decodes nothing for the caller."""
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError("raw must be bytes: the rule is about the bytes that were sent.")
    _bound(max_bytes, "max_bytes", 1, BYTES_CEILING)
    _bound(max_depth, "max_depth", 0, DEPTH_CEILING)
    raw = bytes(raw)
    if len(raw) > max_bytes:
        raise StrictJsonRefused("size", max_bytes, "The text is longer than %d bytes." % max_bytes)
    bad = utf8_fault(raw)
    if bad >= 0:
        raise StrictJsonRefused("utf8", bad, "The text is not well-formed UTF-8.")
    s = _Scan(raw, max_depth)
    top = s.value(0)
    s.ws()
    if s.i != s.n:
        raise StrictJsonRefused("syntax", s.i, "There is data after the JSON value.")
    return {"top": top, "depth": s.depth, "objects": s.objects, "members": s.members}
