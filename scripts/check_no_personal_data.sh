#!/usr/bin/env bash
# Fail if anything personal is in this working tree. Run by sync_from_private.sh and
# usable standalone before a commit:
#
#   scripts/check_no_personal_data.sh [/path/to/private/houndstooth]
#
# With the private path, the real Drive folder id is read from its config.yaml and
# added to the pattern set (it is never written into this repo). The literal patterns
# below are spelled with a bracketed character so this file does not match itself.
set -euo pipefail
ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRIVATE="${1:-}"

patterns=(
  '@g[m]ail'          # any gmail address
  'dm[d]omingo'       # owner handle
  '[H]ologic'         # real employer / posting used in the owner's tests
  '[w]atchlist'       # the private watch-list file and references to it
  '[D]rive folder shared with'   # the private config's folder-id comment
)
if [ -n "$PRIVATE" ] && [ -f "$PRIVATE/config.yaml" ]; then
  fid="$(sed -n 's/^[[:space:]]*gdrive_folder_id:[[:space:]]*"\{0,1\}\([A-Za-z0-9_-]\{10,\}\)"\{0,1\}.*/\1/p' "$PRIVATE/config.yaml" | head -1 || true)"
  [ -n "$fid" ] && patterns+=("$fid")
fi

fail=0
regex="$(IFS='|'; echo "${patterns[*]}")"
if hits="$(grep -rIn -i -E -e "$regex" "$ENGINE" \
      --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=.pytest_cache \
      --exclude-dir=tailored_output --exclude-dir=reports 2>/dev/null)"; then
  echo "FORBIDDEN PATTERN HITS:"; echo "$hits"; fail=1
fi

# Files that must never exist in the public tree.
bad_paths=()
while IFS= read -r -d '' p; do bad_paths+=("${p#"$ENGINE"/}"); done < <(
  find "$ENGINE" \( -path "$ENGINE/.git" -o -path "$ENGINE/.venv" \) -prune -o \
       \( -name '*.db' -o -name '*.sqlite' -o -name 'config.yaml' -o -name 'MANIFEST.md' \
          -o -path "$ENGINE/reports" -o -path "$ENGINE/docs/plan-2026-10" -o -path "$ENGINE/docs/dev" \
          -o -path "$ENGINE/profile/resume_profile.md" -o -path "$ENGINE/profile/resume_template.json" \
          -o -path "$ENGINE/profile/projects.json" -o -path "$ENGINE/profile/voice_bank.json" \
          -o -path "$ENGINE/profile/.baseline_state.json" -o -name 'profile_sync.py' -o -name 'test_profile_sync.py' \) -print0)
# gitignored local copies (config.yaml, profile/*.json) are allowed on disk but must not be tracked
tracked_bad=()
for p in "${bad_paths[@]:-}"; do
  [ -z "$p" ] && continue
  if git -C "$ENGINE" ls-files --error-unmatch "$p" >/dev/null 2>&1 || \
     [ -n "$(git -C "$ENGINE" ls-files "$p" 2>/dev/null)" ]; then
    tracked_bad+=("$p")
  fi
done
if [ "${#tracked_bad[@]}" -gt 0 ]; then
  echo "FORBIDDEN TRACKED PATHS:"; printf '  %s\n' "${tracked_bad[@]}"; fail=1
fi

if [ "$fail" -ne 0 ]; then echo "gate: FAIL"; exit 1; fi
echo "gate: PASS (${#patterns[@]} patterns, no forbidden tracked paths)"
