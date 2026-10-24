#!/usr/bin/env bash
# Pull new or changed briefs (YYYY-MM-DD.md) from a Drive folder into briefs/.
#
#   TOKEN=… FOLDER_ID=… drive-sync.sh
#
# State is STATE_FILE: one "id<TAB>modifiedTime" line per brief already pulled,
# so a file moved into the folder is caught even if its modifiedTime is old.
# Prints the names pulled to stdout; exits non-zero on any failure.
set -euo pipefail

: "${TOKEN:?}" "${FOLDER_ID:?}"
STATE_FILE=${STATE_FILE:-.drive-sync/seen.tsv}
DEST=${DEST:-briefs}
API=${API:-https://www.googleapis.com/drive/v3}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# List every page
: > "$work/files.json"
page=""
while :; do
  curl -sS --fail-with-body -G "$API/files" \
    -H "Authorization: Bearer $TOKEN" \
    --data-urlencode "q='${FOLDER_ID}' in parents and trashed=false" \
    --data-urlencode "fields=nextPageToken,files(id,name,mimeType,modifiedTime)" \
    --data-urlencode "pageSize=1000" \
    ${page:+--data-urlencode "pageToken=$page"} > "$work/page.json"
  jq -c '.files[]' "$work/page.json" >> "$work/files.json"
  page=$(jq -r '.nextPageToken // empty' "$work/page.json")
  [ -n "$page" ] || break
done

# Briefs only: dated .md names, no Google-native files
jq -sr '.[]
  | select(.name | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}\\.md$"))
  | select(.mimeType | startswith("application/vnd.google-apps.") | not)
  | [.id, .name, .modifiedTime] | @tsv' "$work/files.json" | sort -t $'\t' -k2,2 > "$work/briefs.tsv"

dupes=$(cut -f2 "$work/briefs.tsv" | uniq -d)
if [ -n "$dupes" ]; then
  echo "::error::Duplicate names in Drive folder: $(echo $dupes)" >&2
  exit 1
fi

mkdir -p "$(dirname "$STATE_FILE")"
touch "$STATE_FILE"
mkdir -p "$DEST"
while IFS=$'\t' read -r id name mtime; do
  grep -qxF "$id"$'\t'"$mtime" "$STATE_FILE" && continue
  curl -sS --fail-with-body -H "Authorization: Bearer $TOKEN" \
    "$API/files/$id?alt=media" -o "$work/dl"
  mv "$work/dl" "$DEST/$name"
  echo "$name"
done < "$work/briefs.tsv"

cut -f1,3 "$work/briefs.tsv" > "$STATE_FILE"
