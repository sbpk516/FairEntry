# Investors dashboard

Open `web/investors.html` through the existing web server. The overview leads with research candidates and recent disclosures. Select a source, adjust numeric criteria, open a stock for evidence and risks, or switch to All holdings to see every imported position, including unmapped securities, options and historical records. Research inbox preserves articles without a verified ticker association. The stock dialog links to existing FairEntry stock research.

## Run and configure

```powershell
python scripts/investors.py --enrich-limit 20
python scripts/investors.py --offline --enrich-limit 0
python -m http.server 8796 --directory web
```

The refresh writes `web/data/investors.json` and persists its ledger in `data/investors.db`. Both are generated local artifacts. `scripts/build_all.py` also refreshes this dashboard. Source failures are isolated and shown as unavailable; cached reports remain accessible with their dates. An offline export never fetches disclosures. Optional Keith Gill coverage is enabled with `--optional`; it does not assert current ownership.

- `config/investors.yaml`: source identities, links, public feeds, title aliases, polling limits, freshness and default screening criteria.
- `SEC_CONTACT_EMAIL`: a real contact email is required for SEC requests. No placeholder identity is sent. Runtime submissions metadata must match the configured reporting entity before ingestion.
- Financial enrichment reuses the FairEntry store and adds bounded Yahoo queries for candidate tickers. Retrieval dates, price observation dates and source links are exported. Unknown fiscal reporting dates remain unknown. No stock is added to the production screening universe by this feature.
- OpenFIGI maps exact CUSIPs to unambiguous US equity tickers, at most 100 unresolved CUSIPs per default run. Mapping coverage builds over successive runs. Unmapped positions remain in All holdings. Manual reviewed mappings can resolve gaps.

Public MBI Deep Dives and Michael Burry feeds supply article titles, dates and original links. An explicit company title can create a research candidate without disclosed ownership. Feed content is not treated as a verified bullish thesis; paid article text is never copied. Other accepted managers have holdings ingestion and source links; their letters/theses require reviewed imports. Scion is explicitly historical-only pending a reviewed coverage change. Berkshire and Gotham are labeled as firm portfolios, not personal picks.

## Screening and evidence

Criteria include drawdown from the past year's adjusted closing high (%), profit margin (%), revenue growth (%), debt/equity (times), price/free cash flow (times), disclosed portfolio weight (%) and investor overlap (count). Defaults of zero for the two ownership filters allow research-only ideas. Each result uses the same criterion evaluation as its displayed explanations. Consider for research means all configured checks passed; Watch means a non-fundamental filter failed; Avoid under current criteria identifies a failed profitability, debt or cash-flow valuation check. Missing or stale required evidence produces Insufficient data. These are transparent screening rules, not calibrated return probabilities or personalized investment instructions.

Details distinguish sourced facts, FairEntry model estimates and screening interpretations. Price-decline causes, acquisition cost, trade dates and thesis claims remain unknown without supporting evidence. Original research links, existing business analysis, explicit risks and conditions to review the thesis accompany numeric checks. Historical model estimates carry their original build date and a stale warning.

## Holdings and amendments

The SEC adapter downloads official submissions, cover XML and information tables, checks table totals and entry counts, and retains CUSIP, share class, share type and put/call identity. Values use the official filing-date unit convention (thousands before January 3, 2023; dollars afterward). Shares and reported values are distinct; absent values are null. Weights describe only covered reported securities.

Comparisons require consecutive comparable quarterly reports. RESTATEMENT amendments replace the report; NEW HOLDINGS amendments add disjoint securities. Incomplete or ambiguous amendment chains fail closed. Changes compare shares, not market values; quarterly differences do not expose all intervening transactions. Verified split ratios can normalize comparisons. Unreviewed corporate actions remain a caveat. Revisions do not produce trade alerts, and affected pending alerts are superseded even if a revision eliminates every change.

## Reviewed imports

Edit `config/investor_research.json`. A research item uses an accepted `source_id` from the YAML and this schema:

```json
{
  "source_id": "mbi",
  "evidence_type": "research",
  "public_summary": true,
  "title": "Reviewed company analysis",
  "url": "https://original-publisher.example/article",
  "published_at": "2026-09-28T12:00:00Z",
  "tickers": ["UBER"],
  "thesis": "Your brief, independently written and source-supported summary",
  "risks": ["A sourced material risk"],
  "invalidation": ["A measurable condition that would weaken the thesis"],
  "price_decline_reason": null,
  "stance": "not inferred"
}
```

Only explicitly public summaries are exported. Do not put confidential subscriber content in this repository or its public output. Removing an imported item removes its managed ledger entry on the next build. `corporate_actions` accepts objects with `cusip`, `date`, positive `ratio` (new shares / old shares), and an original `url`. `security_mappings` accepts `cusip`, `ticker` and source `url`. Review these identity assertions before adding them; they influence comparisons or ticker associations.

## Email and scheduled processing

Sending requires all of:

- `INVESTORS_EMAIL_ENABLED=true` (GitHub Actions repository variable).
- Explicit `INVESTORS_ALERT_EMAIL` (secret); the old application recipient is not reused implicitly.
- Existing Resend credentials (`RESEND_API_KEY`, `RESEND_FROM_EMAIL`) or the existing SMTP settings in `.env.example`.

No test email is sent by building the dashboard. Local `python scripts/investors.py --alerts` processes eligible notifications when configured. The hourly `investors.yml` workflow polls sources, reserves notifications, saves and verifies the delivery guard, then dispatches through the existing email transport. It shares a concurrency lock and dedicated ledger cache with `refresh.yml`. Hourly processing uploads a review artifact; the visible site updates with the existing site build/deployment schedule, not every hourly poll. GitHub scheduling can be delayed.

Initial imports and historical backfills are suppressed. Source/period/security event IDs deduplicate repeat processing. Amendments supersede unsent changes. Pending disclosures expire after seven days so enabling email later does not release an old backlog. A successful delivery is recorded; a transport return indicating no send remains retryable. Provider exceptions, interrupted or ambiguous delivery is marked `delivery_unknown` and requires operator review, not automatic retry. A unique CI attempt token and persisted reservation guard protect retries after runner interruptions. This favors avoiding duplicates over guaranteed delivery; it does not promise exactly-once email. GitHub caches can be evicted: a missing ledger establishes a new silent baseline and can miss alerts. A durable database service is preferable if alert delivery becomes operationally critical.

## Verification and live limitations

```powershell
python -m pytest tests/test_investors.py tests/test_alerts.py
node tests/investors-model.test.cjs
```

Tests cover security identities, value units, share changes, missing fields, splits, amendments, baseline suppression, replay protection, failure isolation and filtering explanations. Public feeds and candidate financial retrieval were exercised locally; browser checks covered stock details and changing criteria. Live SEC ingestion needs the real contact setting, and live email needs recipient/provider configuration. Neither live email delivery nor deployment was performed as part of this implementation. The scheduled workflows need verification in GitHub Actions after configuration.
