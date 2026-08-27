#!/usr/bin/env bash
# Render ainglish-whitepaper.pdf from ainglish-whitepaper.md.
#
# The Markdown document is the source of truth; this script only typesets it and
# never modifies it. The toolchain is pinned by image digest and the PDF
# timestamp is taken from the owner-approval date in the document, so repeated
# runs over unchanged source produce byte-identical output.
#
# Usage: ./build_pdf.sh [--check]
#   --check  rebuild to a temporary file and fail if it differs from the
#            committed PDF (used by CI)
set -euo pipefail
cd "$(dirname "$0")"

# pandoc 3.10, TeX Live 2026
IMAGE='pandoc/latex@sha256:f5e8002f6cdec21dcd000b23817fd385d4db8234fbbbb54c43c2c173d9fa2d71'
SRC='ainglish-whitepaper.md'
OUT='ainglish-whitepaper.pdf'
[ "${1:-}" = '--check' ] && OUT='.wp-check.pdf'

die() { printf 'build_pdf: %s\n' "$1" >&2; exit 2; }

# Fail closed on front-matter drift: the title block is parsed out of the
# document rather than hardcoded here, so a reworded head must be deliberate.
line() { sed -n "${1}p" "$SRC"; }
[ -f "$SRC" ] || die "missing $SRC"
case "$(line 1)" in '# '*) ;; *) die 'line 1 must be the H1 title' ;; esac
[ -z "$(line 2)" ] || die 'line 2 must be blank'
case "$(line 3)" in '**Whitepaper, version '*'**') ;; *) die 'line 3 must be the version subtitle' ;; esac
[ -z "$(line 4)" ] || die 'line 4 must be blank'
case "$(line 5)" in '*Author:*'*) ;; *) die 'line 5 must be the attribution line' ;; esac
case "$(line 6)" in '*Status:*'*) ;; *) die 'line 6 must be the status line' ;; esac

TITLE=$(line 1 | sed 's/^# //')
# The attribution line is one long line; split it at its separators so the title
# block breaks it into lines instead of running off the page. A trailing
# backslash is a hard line break in the same reader that renders the body.
AUTHOR=$(line 5 | awk '{n=split($0,a," · "); for(i=1;i<=n;i++) printf "  %s%s\n", a[i], (i<n?"\\":"")}')
SUBTITLE=$(line 3 | sed 's/^\*\*//; s/\*\*$//')
APPROVED=$(grep -oE 'approved by the owner [0-9]{4}-[0-9]{2}-[0-9]{2}' "$SRC" | head -1 \
  | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}') || true
[ -n "${APPROVED:-}" ] || die 'no owner-approval date found in the document'

# Fixed temporary names, not mktemp: a random filename would leak into the
# output and break byte-reproducibility.
BODY='.wp-body.md'; META='.wp-meta.yaml'; LOG='.wp-build.log'
trap 'rm -f "$BODY" "$META" "$LOG"' EXIT
tail -n +6 "$SRC" > "$BODY"   # drop the H1/subtitle/attribution block; the status line stays in the body
{
  printf -- '---\n'
  printf 'title: |\n  %s\n' "$TITLE"
  printf 'subtitle: |\n  %s\n' "$SUBTITLE"
  printf 'author: |\n%s\n' "$AUTHOR"
  printf 'date: |\n  %s\n' "$APPROVED"
  printf -- '---\n'
} > "$META"

set +e
docker run --rm -e HOME=/tmp \
  -e "SOURCE_DATE_EPOCH=$(date -u -d "$APPROVED" +%s)" -e SOURCE_DATE_EPOCH_TEX_PRIMITIVES=1 \
  -v "$PWD:/data" -w /data --entrypoint pandoc "$IMAGE" \
  "$META" "$BODY" -o "$OUT" \
  --from=gfm+smart --pdf-engine=xelatex \
  --lua-filter=pdf-tables.lua \
  --table-of-contents --toc-depth=2 \
  --include-in-header=pdf-preamble.tex \
  -V documentclass=article -V papersize=a4 -V geometry:margin=1.8cm \
  -V fontsize=10pt -V colorlinks=true -V linkcolor=Maroon -V urlcolor=Maroon \
  -V toccolor=black -V links-as-notes=false 2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}
set -e
[ "$status" -eq 0 ] || die 'pandoc failed'
# XeTeX omits a glyph its font lacks without failing, so treat that as fatal.
if grep -q 'Missing character' "$LOG"; then
  grep -o 'Missing character: There is no [^ ]* (U+[0-9A-F]*)' "$LOG" | sort -u >&2
  die 'a glyph was dropped silently — add a mapping to pdf-preamble.tex'
fi

if [ "${1:-}" = '--check' ]; then
  # Bytes are not comparable: xdvipdfmx randomises font subset tags on every run.
  # pdf_normalise.py digests the document content with those tags, each stream's
  # compressed /Length and the cross-reference offsets normalised away.
  fresh=$(./pdf_normalise.py "$OUT" | cut -d' ' -f1)
  committed=$(./pdf_normalise.py 'ainglish-whitepaper.pdf' | cut -d' ' -f1)
  rm -f "$OUT"
  [ "$fresh" = "$committed" ] || die "ainglish-whitepaper.pdf does not match a fresh build of the source ($committed != $fresh)"
  echo "pdf up to date (content digest $committed)"
  exit 0
fi
printf 'wrote %s (%s bytes, sha256 %s)\n' "$OUT" "$(stat -c%s "$OUT")" "$(sha256sum "$OUT" | cut -d" " -f1)"
