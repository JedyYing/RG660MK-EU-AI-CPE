#!/usr/bin/env bash
# Parallel ranged download from ModelScope, resumable, then concatenate the parts.
# usage: dl-parallel.sh <url> <total-bytes> <out-file> [parts] [parallel]
set -u
URL="$1"; TOTAL="$2"; OUT="$3"; PARTS="${4:-16}"; PAR="${5:-8}"
DIR="${OUT}.parts"
mkdir -p "$DIR"

CHUNK=$(( (TOTAL + PARTS - 1) / PARTS ))
JOBS=""
for i in $(seq 0 $((PARTS-1))); do
  START=$(( i * CHUNK ))
  END=$(( START + CHUNK - 1 ))
  [ "$END" -ge "$TOTAL" ] && END=$(( TOTAL - 1 ))
  [ "$START" -ge "$TOTAL" ] && break
  PART="$DIR/part-$(printf '%02d' $i)"
  WANT=$(( END - START + 1 ))
  if [ -f "$PART" ] && [ "$(stat -c%s "$PART")" -eq "$WANT" ]; then
    echo "part $i already complete"
  else
    echo "$START $END $PART" >> "$DIR/jobs.txt"
  fi
done
sort -u -o "$DIR/jobs.txt" "$DIR/jobs.txt" 2>/dev/null || true

run_one() {
  read -r START END PART <<< "$1"
  WANT=$(( END - START + 1 ))
  curl -sL --retry 5 --retry-delay 2 -r "$START-$END" -o "$PART.tmp" "$URL" \
    && [ "$(stat -c%s "$PART.tmp")" -eq "$WANT" ] && mv -f "$PART.tmp" "$PART" \
    && echo "done $PART ($WANT bytes)" || echo "FAILED $PART"
}
export -f run_one
export URL

if [ -s "$DIR/jobs.txt" ]; then
  # shellcheck disable=SC2002
  cat "$DIR/jobs.txt" | xargs -d '\n' -P "$PAR" -I{} bash -c 'run_one "$@"' _ "{}"
fi

echo "=== assembling ==="
: > "$OUT"
for f in $(ls "$DIR"/part-* | sort); do
  cat "$f" >> "$OUT"
done
SZ=$(stat -c%s "$OUT")
echo "assembled $OUT = $SZ bytes (expected $TOTAL)"
if [ "$SZ" -ne "$TOTAL" ]; then echo "SIZE MISMATCH — not removing parts"; exit 1; fi
echo "sha256: $(sha256sum "$OUT" | cut -d' ' -f1)"
rm -rf "$DIR"
echo "parts cleaned"
