#!/bin/sh
# Give a detached staged snapshot discoverable repository identity, then clear every inherited
# Git-local override so nested repository fixtures remain isolated.
set -u

SCOPED_GIT_DIR=$(git rev-parse --absolute-git-dir) || exit 1
SCOPED_WORK_TREE=$(git rev-parse --show-toplevel) || exit 1

if [ ! -e "$SCOPED_WORK_TREE/.git" ]; then
    printf 'gitdir: %s\n' "$SCOPED_GIT_DIR" > "$SCOPED_WORK_TREE/.git" || exit 1
fi

for name in $(git rev-parse --local-env-vars); do
    unset "$name"
done

exec "$@"
