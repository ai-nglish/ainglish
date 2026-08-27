#!/usr/bin/env python3
"""Content digest of a PDF, ignoring xdvipdfmx's random font subset tags.

The PDF build is byte-reproducible except for one thing: xdvipdfmx picks a fresh
random six-letter subset tag per embedded font on every run (AXAPHO+LMRoman10 …),
and that tag lands in the font dictionaries, the ToUnicode CMaps and the embedded
font programs. There is no xdvipdfmx option to fix it, so instead of claiming
byte determinism this rewrites every tag to a constant, drops the two things that
merely follow from compression (each stream's /Length and the cross-reference
table of byte offsets), and digests what is left. Two builds of unchanged source
must produce the same digest.

Usage: pdf_normalise.py FILE...   # prints "<digest>  <file>" per argument
"""
import hashlib
import re
import sys
import zlib

TAG = re.compile(rb"/[A-Z]{6}\+")
LENGTH = re.compile(rb"/Length\s+\d+")
OBJ = re.compile(rb"(\d+) 0 obj(.*?)endobj", re.S)
STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def normalise(path):
    data = open(path, "rb").read()
    parts = []
    for match in sorted(OBJ.finditer(data), key=lambda m: int(m.group(1))):
        number, body = int(match.group(1)), match.group(2)
        # The cross-reference stream is a table of byte offsets, so it moves
        # whenever a compressed length moves. It carries no document content.
        if b"/Type/XRef" in body:
            continue
        stream = STREAM.search(body)
        if stream:
            head, raw = body[: stream.start()], stream.group(1)
            try:
                content = zlib.decompress(raw)
            except zlib.error:
                content = raw
        else:
            head, content = body, b""
        # /Length is the size of the COMPRESSED stream, which shifts when a
        # subset tag compresses differently, so it is normalised away too.
        head = LENGTH.sub(b"/Length 0", TAG.sub(b"/AAAAAA+", head))
        parts.append(b"%d\x00%s\x00%s" % (number, head,
                                          TAG.sub(b"/AAAAAA+", content)))
    return hashlib.sha256(b"\x01".join(parts)).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for path in sys.argv[1:]:
        print("%s  %s" % (normalise(path), path))
