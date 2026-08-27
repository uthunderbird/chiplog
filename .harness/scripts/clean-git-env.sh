#!/bin/sh
# Run repository-creating verifiers without inheriting the caller hook's Git scope.
# The gate's index-sensitive checks run before this wrapper and retain GIT_INDEX_FILE.
set -u

for name in $(git rev-parse --local-env-vars); do
    unset "$name"
done

exec "$@"
