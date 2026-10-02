#!/bin/sh
# Durable wrapper is implemented in Python for atomic JSON and signal handling.
exec python3 "$(dirname "$0")/run-selector-durable.py" "$@"
