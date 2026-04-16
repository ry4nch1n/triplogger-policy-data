# TripLogger Policy Data

Visa rules, tax thresholds, and travel-document data for the [TripLogger](https://github.com/ry4nch1n/triplogger) Android app.

## Structure

```
manifest.json                   # List of supported countries
rules/
  {COUNTRY}_{PASSPORT}.json     # e.g. CN_SG.json — China rules for Singapore passport
documents/
  {COUNTRY}.json                # e.g. CN.json — China travel documents
schema/
  manifest.schema.json          # JSON Schema Draft 7
  rule.schema.json
  document.schema.json
.github/
  workflows/
    validate-policy.yml         # CI: validates every JSON file against schema on push/PR
    policy-research.yml         # Daily autonomous research + PR
  scripts/
    research.py                 # Claude-API-backed issue-to-PR pipeline
    requirements.txt
```

## How the app consumes this repo

The TripLogger app fetches these files on launch (once per 24h, or on demand via Settings → Policy Data → Refresh):

1. `GET manifest.json` — discovers supported countries
2. For each country, `GET rules/{CC}_{PP}.json` + `GET documents/{CC}.json`
3. Parsed with `Json { ignoreUnknownKeys = true }` — unknown fields are silently dropped, so additive schema changes are safe

When offline, the app uses its bundled defaults shipped in the APK.

## Autonomous research pipeline

When a TripLogger user adds a trip to an unsupported country, the app opens a GitHub issue here labelled `missing-country`. A daily workflow (`policy-research.yml`) picks up those issues, calls Claude Sonnet 4.6 with web search to draft rule/document JSON grounded in official sources, validates the output against the schemas above, and opens a PR for human review.

**Important**: auto-generated PRs are NEVER auto-merged. Visa/tax data has real legal consequences — a human must review every PR. The PR body includes the list of sources Claude cited.

Triggers:
- `schedule` — daily at 02:17 UTC (off-peak minute to avoid cron bunching)
- `issues: opened` — fires immediately when a new issue lands
- `workflow_dispatch` — manual run, with optional single-issue override

Secrets required: `ANTHROPIC_API_KEY`. The default `GITHUB_TOKEN` covers all repo operations.

Cost envelope: ~$0.05–0.10 per issue, capped at 10 issues per scheduled run.

## Contributing

1. Fork
2. Edit JSON files following the schemas in `schema/`
3. Submit a PR — the `validate-policy` CI must pass

### Adding a new country manually

1. Add an entry to `manifest.json`
2. Create `rules/{CC}_SG.json` (SG-passport rules for now; other passports later)
3. Create `documents/{CC}.json`
4. `check-jsonschema --schemafile schema/rule.schema.json rules/*.json` locally before pushing

### Adding city-specific action steps

`actionStep.location` can be:
- `"online"` — shown to all users
- `"SG"` / `"JP"` / other ISO codes — shown when the user is based there
- City name (e.g. `"Shanghai"`, `"Beijing"`) — shown when the user is in that city

## Schema

See [SCHEMA.md](SCHEMA.md) for the human-readable field reference. `schema/*.schema.json` is the authoritative machine-validated source.

## Disclaimer

Data here is community-maintained and provided for general guidance only. Always verify with official government sources before making travel or tax decisions.
