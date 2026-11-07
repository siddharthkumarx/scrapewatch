#!/usr/bin/env sh
# A scraper in plain shell (curl + awk): books.toscrape.com catalogue pages to JSON lines on
# stdout. Any language works the same way: write rows to a file, then `scrapewatch check` it.
# Usage: ./scrape.sh [pages] > books.jsonl
set -eu
pages="${1:-3}"
base="https://books.toscrape.com/catalogue/"
i=1
while [ "$i" -le "$pages" ]; do
  curl -fsS --compressed -A "scrapewatch-example/0.1" "${base}page-${i}.html" |
    awk -v base="$base" '
      function attr(line, name,    m) {
        if (match(line, name "=\"[^\"]*\"")) {
          m = substr(line, RSTART + length(name) + 2, RLENGTH - length(name) - 3)
          return m
        }
        return ""
      }
      function esc(s) { gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); return s }
      /<h3><a href=/ { href = attr($0, "href"); title = attr($0, "title") }
      /class="price_color"/ {
        price = $0
        sub(/.*class="price_color">/, "", price)
        sub(/<.*/, "", price)
        printf "{\"title\": \"%s\", \"price\": \"%s\", \"url\": \"%s%s\"}\n", esc(title), esc(price), base, esc(href)
      }'
  i=$((i + 1))
done
