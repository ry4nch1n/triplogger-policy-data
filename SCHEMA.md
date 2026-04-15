# JSON Schema Reference

## Rules File (`rules/{COUNTRY}_{PASSPORT}.json`)

Array of rule objects:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `countryCode` | string | yes | ISO 3166-1 alpha-2 (e.g., "CN") |
| `passportCountry` | string | yes | Passport holder's country (e.g., "SG") |
| `ruleType` | string | yes | One of: `SINGLE_ENTRY_LIMIT`, `ROLLING_WINDOW_LIMIT`, `CALENDAR_YEAR_THRESHOLD`, `CONSECUTIVE_YEAR_RULE`, `VISA_REQUIREMENT` |
| `displayName` | string | yes | Short name shown in app |
| `description` | string | yes | Brief description |
| `thresholdDays` | int | yes | Day limit for this rule |
| `windowDays` | int | no | For rolling window rules (e.g., 180) |
| `consecutiveYears` | int | no | For consecutive year rules (e.g., 6) |
| `resetConditionDays` | int | no | Days outside country to reset counter |
| `cautionThresholdPercent` | int | no | Default: 70 |
| `warningThresholdPercent` | int | no | Default: 85 |
| `criticalThresholdPercent` | int | no | Default: 95 |
| `applicableVisaTypes` | string[] | no | Null = applies to all. E.g., `["VISA_FREE"]` |
| `validFrom` | string | no | ISO date (YYYY-MM-DD) |
| `validUntil` | string | no | ISO date (YYYY-MM-DD) |
| `isActive` | boolean | no | Default: true |
| `priority` | int | no | Display order (lower = higher) |
| `suggestion` | string | no | Short suggestion shown on dashboard |
| `detailedExplanation` | string | no | Full explanation shown on detail screen |
| `actionSteps` | ActionStep[] | no | Recommended actions |

### ActionStep

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | yes | Action name |
| `description` | string | yes | What to do |
| `url` | string | no | Website link |
| `urlMobile` | string | no | Mobile-specific link |
| `location` | string | no | `"online"`, city name, or country code |
| `address` | string | no | Physical address |
| `isOnline` | boolean | no | Default: true |

## Documents File (`documents/{COUNTRY}.json`)

Array of document objects:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `countryCode` | string | yes | ISO 3166-1 alpha-2 |
| `name` | string | yes | Document name |
| `description` | string | yes | What this document is for |
| `url` | string | yes | Official URL to fill/download |
| `urlMobile` | string | no | Mobile-friendly URL |
| `reminderDaysBefore` | int | no | Default: 3. When to remind before trip |
| `isRequired` | boolean | no | Default: true |
| `applicableVisaTypes` | string[] | no | Null = all visa types |
| `category` | string | no | `"arrival"`, `"customs"`, `"health"`, `"visa"` |
