#!/usr/bin/env sh
# Scrape with the shell scraper, then check the file: exit 0 pass, 1 warn, 2 hold, 3 error.
set -eu
cd "$(dirname "$0")"
mkdir -p output
sh scrape.sh "${1:-3}" > output/books.jsonl
scrapewatch check output/books.jsonl --rules rules.yml \
  --report output/report.html --json output/summary.json
