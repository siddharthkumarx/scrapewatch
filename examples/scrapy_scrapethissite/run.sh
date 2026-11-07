#!/usr/bin/env sh
# Run the example from the repository root or from this directory.
set -eu
cd "$(dirname "$0")"
mkdir -p output
scrapy runspider spider.py -O output/countries.jsonl
