# Cost golden: hand computation

`events_small.jsonl` (14 events) priced with `cost_config.yml`, plus 120 browser seconds,
10 usable records out of 12. Formulas: docs/architecture.md section 11.2.

Event costs (`wire_bytes / 1e9 × per_gb`, or `per_1000_requests / 1000` for an API label):

| Event | Domain | Proxy | Type | Bytes | Cost | Waste category |
|---|---|---|---|---|---|---|
| e01 | books | datacenter (0.8) | document | 1,000,000 | 0.0008 | |
| e02 | books | datacenter | document | 2,000,000 | 0.0016 | |
| e03 | books | datacenter | document | 500,000 | 0.0004 | failed (503) |
| e04 | books | datacenter | document | 1,000,000 | 0.0008 | retry (attempt 2) |
| e05 | quotes | residential (8.0) | document | 500,000 | 0.004 | |
| e06 | quotes | residential | image | 250,000 | 0.002 | blockable |
| e07 | quotes | residential | font | 125,000 | 0.001 | blockable |
| e08 | quotes | residential | document | 100,000 | 0.0008 | failed (challenge) |
| e09 | quotes | unblocker (API, 2.5 / 1000) | document | 3,000,000 | 0.0025 | |
| e10 | quotes | datacenter | document | 50,000 | 0.00004 | unchanged refetch |
| e11 | books | mystery-pool (unknown) | document | 1,000,000 | 0 | (finding cost.unknown_label) |
| e12 | scrapethissite | direct (0) | document | 400,000 | 0 | |
| e13 | quotes | residential | image | 1,000,000 | 0.008 | retry (also failed, unchanged, blockable: retry wins) |
| e14 | books | datacenter | xhr | 0 | 0 | failed (transport error) |

Sums:

- Events: books 0.0036, quotes 0.01834, scrapethissite 0; all events 0.02194.
- Browser: 2 minutes × 0.01 = 0.02, spread by share of document responses: books 5/10,
  quotes 4/10, scrapethissite 1/10 → 0.01, 0.008, 0.002.
- By domain: books 0.0136, quotes 0.02634, scrapethissite 0.002. Total 0.04194.
- By proxy (events only): datacenter 0.00364, residential 0.0158, unblocker 0.0025,
  mystery-pool 0, direct 0.
- By resource type (events only): document 0.01094, image 0.01, font 0.001, xhr 0.
- Per usable record 0.04194 / 10 = 0.004194; per record 0.04194 / 12 = 0.003495.

Waste: retry 2 requests, 2,000,000 bytes, 0.0088; failed 3, 600,000, 0.0012; unchanged 1,
50,000, 0.00004; blockable 2, 375,000, 0.003; total 0.01304. Bytes by type: document
9,550,000, image 1,250,000, font 125,000, xhr 0. Conditional hits: 1 (e10).
