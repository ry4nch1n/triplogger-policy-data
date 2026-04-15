# TripLogger Policy Data

Community-editable visa rules, tax thresholds, and travel document data for the [TripLogger](https://github.com/ry4nch1n/triplogger-policy-data) Android app.

## Structure

```
rules/
  {COUNTRY}_{PASSPORT}.json    # e.g., CN_SG.json = China rules for Singapore passport
documents/
  {COUNTRY}.json               # e.g., CN.json = China travel documents
```

## How it works

The TripLogger app fetches these files on launch (once per 24 hours) to get the latest policy data. When offline, the app uses its bundled defaults.

## Contributing

1. Fork this repo
2. Edit or add JSON files following the existing format
3. Submit a PR with a source link for any policy changes

### Adding a new country

1. Create `rules/{COUNTRY}_{PASSPORT}.json` (e.g., `JP_SG.json` for Japan + Singapore passport)
2. Create `documents/{COUNTRY}.json` for travel documents
3. Follow the schema in existing files

### Adding location-specific action steps

Each rule can have `actionSteps` with a `location` field:
- `"online"` — always shown
- `"SG"` — shown when user is based in Singapore
- `"Shanghai"`, `"Beijing"`, `"Shenzhen"` — shown when user is in that city

## Schema

See [SCHEMA.md](SCHEMA.md) for the full JSON schema documentation.

## Disclaimer

This data is community-maintained and provided for general guidance only. Always verify with official government sources before making travel or tax decisions.
