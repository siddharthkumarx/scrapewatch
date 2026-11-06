# Guard rules

The bundled rule set (`src/scrapewatch/guard/rules/guard-rules.yml`, rules_version `2026.11.0`). Format and evaluation: docs/architecture.md section 6. Every rule has a
fixture under `tests/guard/fixtures/`; `pytest tests/guard` (or `scrapewatch rules test`
once the CLI command exists) checks them all.

Signals come from public product signatures only: status codes, header names, cookie names,
widget class names, interstitial titles and script host names. The rules say what answered;
they never describe how to get past it, and rules that do are declined.

When several rules match, the label precedence decides (captcha > challenge > block >
rate_limit > login > consent > redirect > error_page > empty), then confidence, then file order.

| Rule id | Vendor | Label | Confidence | Signal kinds | Notes |
|---|---|---|---|---|---|
| `cloudflare.challenge` | cloudflare | challenge | 0.95 | header `cf-mitigated` / status + header `server` + body marker / status + header `server` + title regex | Managed or JS challenge interstitial. |
| `cloudflare.turnstile` | cloudflare | captcha | 0.9 | status + header `server` + body marker | Turnstile widget on a Cloudflare-served interstitial. |
| `cloudflare.block` | cloudflare | block | 0.9 | status + header `server` + body marker / status + header `server` + title regex | Firewall block page (error 1020 and similar). |
| `akamai.block` | akamai | block | 0.9 | status + header `server` + title regex / status + body marker | Edge 'Access Denied' page with an edgesuite reference. |
| `akamai.challenge` | akamai | challenge | 0.85 | body marker / cookie + text length below | Bot Manager interstitial (sec-cpt container or cookie on a short page). |
| `datadome.captcha` | datadome | captcha | 0.95 | status + body marker / header `x-datadome` + body marker | Interstitial that loads the captcha-delivery widget. |
| `datadome.block` | datadome | block | 0.85 | status + header `x-datadome` / status + cookie + text length below | 403 from a DataDome-protected origin. |
| `human.captcha` | human | captcha | 0.9 | body marker | Human Challenge element on the block page. |
| `human.block` | human | block | 0.85 | status + body marker / status + title regex | 403 block page carrying the PerimeterX app id. |
| `imperva.block` | imperva | block | 0.9 | body marker / status + header `x-cdn` | Incident page with an Incapsula incident id. |
| `imperva.challenge` | imperva | challenge | 0.85 | body marker + text length below | Short page that only loads the _Incapsula_Resource script. |
| `kasada.challenge` | kasada | challenge | 0.85 | status + body marker / header `x-kasada-ac` + text length below | 429 interstitial that loads the KPSDK script. |
| `aws_waf.challenge` | aws_waf | challenge | 0.95 | header `x-amzn-waf-action` / status + body marker | Challenge action (x-amzn-waf-action: challenge, usually HTTP 202). |
| `aws_waf.captcha` | aws_waf | captcha | 0.95 | header `x-amzn-waf-action` | CAPTCHA action (x-amzn-waf-action: captcha, usually HTTP 405). |
| `aws_waf.block` | aws_waf | block | 0.8 | status + header `x-cache` / status + title regex | CloudFront 403 'request could not be satisfied' (WAF or geo block). |
| `hcaptcha.captcha` | hcaptcha | captcha | 0.8 | body marker + text length below | h-captcha widget on a page with little other text. |
| `recaptcha.captcha` | recaptcha | captcha | 0.8 | body marker + text length below | g-recaptcha widget on a page with little other text. |
| `turnstile.captcha` | turnstile | captcha | 0.8 | body marker + text length below | cf-turnstile widget on a page with little other text. |
| `generic.rate_limit.429` | — | rate_limit | 0.9 | status | HTTP 429 Too Many Requests. |
| `generic.rate_limit.text` | — | rate_limit | 0.7 | body marker + text length below / body marker + text length below | A short page that says the client is rate limited. |
| `generic.block.403` | — | block | 0.5 | status | Any 403 no vendor rule explains. |
| `generic.login.401` | — | login | 0.6 | status | HTTP 401: the page needs credentials. |
| `generic.error_page.5xx` | — | error_page | 0.6 | status | Server error status no vendor rule explains. |
| `generic.login.wall` | — | login | 0.75 | has form password + final url regex / has form password + title regex + text length below | A password form after a redirect to a login path, or on a short sign-in page. |
| `generic.consent.wall` | — | consent | 0.6 | body marker + text length below / body marker + text length below / body marker + text length below | Consent banner text on a page with little other text. |
| `generic.empty.small_vs_baseline` | — | empty | 0.7 | content type + size below baseline pct + text density below + has captcha widget | HTML under 30% of the usual size for this path, almost no text, no widget. |
| `generic.empty.tiny` | — | empty | 0.6 | status + content type + size below + text length below + has captcha widget | An HTML document under 2,048 bytes with under 200 characters of visible text. |
| `generic.error_page.title` | — | error_page | 0.65 | status + title regex + text length below | Status 200 but the title names a server error, on a short page. |
| `generic.error_page.soft_404` | — | error_page | 0.65 | status + title regex | Status 200 but the title says the page does not exist (soft 404). |
| `generic.redirect.login` | — | redirect | 0.7 | final url regex | Final URL is a login path. |
| `generic.redirect.geo` | — | redirect | 0.7 | final url regex / final url regex | Final URL is a region selector or a not-available-in-your-country page. |
| `generic.redirect.currency` | — | redirect | 0.6 | final url regex | Final URL switches the store currency. |
| `generic.redirect.consent` | — | redirect | 0.6 | final url regex | Final URL is a consent host or path. |

## Adding a rule

1. `scrapewatch guard features saved-response.html --status 403` prints the fixture-safe
   features of a response you saved; keep only what the rule needs.
2. Add the rule to `guard-rules.yml` and bump `rules_version`.
3. Add `tests/guard/fixtures/<rule id>.yml` with `rule`, `expect` and `features`; add a
   negative fixture (`expect: {label: ok}`) when the signal could appear on normal pages.
4. Run `pytest tests/guard`. In the pull request, name the product the signals come from and
   confirm no page body is included.
