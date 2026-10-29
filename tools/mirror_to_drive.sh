#!/usr/bin/env bash
# Mirror the archive's git-tracked contents to the archive account's Google Drive.
#
# The mirror is a backup, not a place to share or contribute: git (GitHub) is the
# authoritative archive and the only place changes are made. See README.md, "Storage".
#
# The daily archive job runs it from the ops repository, which holds the Drive credential; this
# repository never does. It can also be run by hand.
#
# Usage: tools/mirror_to_drive.sh [--dry-run]
# Requires an rclone remote named bpr-drive signed in as berkeleypublicrecords@gmail.com.
set -euo pipefail
cd "$(dirname "$0")/.."
REMOTE="bpr-drive:berkeley-public-records-mirror"
if [ -n "$(git status --porcelain)" ] || [ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]; then
    echo "Mirror only a clean checkout of main." >&2; exit 1
fi
git pull -q --ff-only
COMMIT=$(git rev-parse HEAD)
# Mirror exactly the tracked files; nothing local and untracked goes up.
git ls-files > /tmp/bpr-mirror-files
# --delete-excluded removes anything in the mirror that git no longer tracks, so a withdrawal
# reaches the mirror too.
rclone sync . "$REMOTE" --files-from /tmp/bpr-mirror-files --delete-excluded --checksum "$@"
if [[ " $* " != *" --dry-run "* ]]; then
    rclone check . "$REMOTE" --files-from /tmp/bpr-mirror-files --checksum --one-way
    echo "$COMMIT $(date -u +%Y-%m-%dT%H:%M:%SZ)" | rclone rcat "$REMOTE/MIRRORED_COMMIT.txt"
    echo "Mirrored $COMMIT to $REMOTE"
fi
