#!/usr/bin/env sh
# Run the example from the repository root or from this directory.
set -eu
cd "$(dirname "$0")"
python scrape.py "${1:-3}"
