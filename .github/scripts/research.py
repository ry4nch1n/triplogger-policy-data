#!/usr/bin/env python3
"""Research a `missing-country` or `missing-city` issue and produce policy JSON.

Inputs (env vars):
  ANTHROPIC_API_KEY   — required, set via GitHub Actions secret.
  ISSUE_NUMBER        — GitHub issue number to research.
  ISSUE_BODY_PATH     — path to a file containing the full issue body.
  REPO_ROOT           — checkout root (defaults to cwd).

Outputs:
  missing_country:
    Creates rules/<CC>_<PP>.json and documents/<CC>.json.
    Updates manifest.json to include the new country.
  missing_city:
    Merges city-specific action steps into existing rules/<CC>_<PP>.json.
    Adds city to manifest.json countries[].cities[].

  Both modes print a Markdown "sources" block on stdout for the PR body.

Exit code:
  0 on success, 1 if the issue body is malformed / model output fails schema twice.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml
from anthropic import Anthropic
from jsonschema import Draft7Validator

# Use Sonnet by default — 8x cheaper than Opus, more than adequate for structured-output
# research with web-search grounding. Override via env for one-off testing.
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 16000
RETRY_ATTEMPTS = 2  # once for validation failure, once for retry with error feedback


def die(msg: str) -> None:
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def parse_frontmatter(body: str) -> dict:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", body, re.DOTALL)
    if not match:
        die("Issue body has no YAML frontmatter block.")
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        die(f"Frontmatter is not valid YAML: {exc}")
    if not isinstance(data, dict):
        die("Frontmatter did not parse to a mapping.")
    required = ["request_type", "country_code", "country_name", "passport_country"]
    if data.get("request_type") == "missing_city":
        required.append("city")
    for key in required:
        if key not in data:
            die(f"Frontmatter missing required field: {key}")
    return data


def load_schemas(root: Path) -> tuple[dict, dict, dict]:
    return (
        json.loads((root / "schema" / "rule.schema.json").read_text()),
        json.loads((root / "schema" / "document.schema.json").read_text()),
        json.loads((root / "schema" / "manifest.schema.json").read_text()),
    )


# ---------------------------------------------------------------------------
# Country research (existing)
# ---------------------------------------------------------------------------

def build_country_prompt(fm: dict, rule_schema: dict, doc_schema: dict) -> str:
    country_code = fm["country_code"]
    country_name = fm["country_name"]
    passport = fm["passport_country"]

    return f"""You are researching visa and tax rules for a travel-logging app. Produce two strict JSON documents that a machine will validate against the schemas below. Cite official government sources for every rule.

TARGET: {country_name} ({country_code}) entry rules for a {passport} passport holder.

RULES YOU MAY EMIT (one entry per applicable type):

1. SINGLE_ENTRY_LIMIT — per-entry stay cap (e.g. 30-day visa-free, 60-day M Visa).
   Use `applicableVisaTypes` to scope it to one visa regime.
2. ROLLING_WINDOW_LIMIT — cumulative days in a rolling window (e.g. 90-in-180).
   Requires `windowDays`.
3. CALENDAR_YEAR_THRESHOLD — tax residency by physical presence (typically 183 days).
   Do NOT set `applicableVisaTypes` (tax is about presence, not visa).
4. CONSECUTIVE_YEAR_RULE — multi-year residency that triggers worldwide-income tax.
   Requires `consecutiveYears`. Optional `resetConditionDays`.
5. VISA_REQUIREMENT — signals "you need a proper visa" when the visa-free stay threshold
   is exceeded.

INSTRUCTIONS:

- Use the web_search tool to find authoritative sources. Prefer .gov / embassy / official
  visa-center domains. Do NOT rely on travel blogs.
- Every `actionStep.url` must point to an official source.
- If you cannot find authoritative data for a rule type, OMIT that rule — do not guess.
- Use ISO date format for `validFrom`/`validUntil`.
- Set `priority` 1..N in the order rules should appear on the dashboard.
- Use Singapore context where relevant (e.g. cite IRAS for tax-credit guidance in
  `actionSteps` for CALENDAR_YEAR_THRESHOLD).
- IMPORTANT: Do NOT add actionSteps that duplicate information already covered
  in the documents file. Action steps are for location-specific procedural guidance
  (offices, addresses, visa extension steps). Documents are for universal entry
  paperwork (arrival cards, customs forms, health declarations). If a topic belongs
  in documents (e.g. "Arrival Card", "Customs Declaration"), put it there — not in
  actionSteps. Never put the same topic in both places.

OUTPUT: Return exactly ONE JSON object (no prose, no markdown fences) with three keys:

  {{
    "rules": [ ... array matching rule.schema.json ... ],
    "documents": [ ... array matching document.schema.json ... ],
    "sources": [ {{ "title": "...", "url": "https://..." }}, ... ]
  }}

The "sources" array will be surfaced in the PR body for human review.

RULE SCHEMA:
```json
{json.dumps(rule_schema, indent=2)}
```

DOCUMENT SCHEMA:
```json
{json.dumps(doc_schema, indent=2)}
```
"""


def validate_country(payload: dict, rule_schema: dict, doc_schema: dict) -> list[str]:
    errors = []
    for err in Draft7Validator(rule_schema).iter_errors(payload.get("rules", [])):
        errors.append(f"rules: {err.message} at {list(err.path)}")
    for err in Draft7Validator(doc_schema).iter_errors(payload.get("documents", [])):
        errors.append(f"documents: {err.message} at {list(err.path)}")
    return errors


def write_country_outputs(root: Path, fm: dict, payload: dict) -> None:
    cc, pp = fm["country_code"], fm["passport_country"]
    rules_path = root / "rules" / f"{cc}_{pp}.json"
    docs_path = root / "documents" / f"{cc}.json"
    rules_path.parent.mkdir(exist_ok=True)
    docs_path.parent.mkdir(exist_ok=True)
    rules_path.write_text(json.dumps(payload["rules"], indent=2, ensure_ascii=False) + "\n")
    docs_path.write_text(json.dumps(payload["documents"], indent=2, ensure_ascii=False) + "\n")

    # Upsert manifest
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    existing_codes = {c["countryCode"] for c in manifest["countries"]}
    if cc not in existing_codes:
        manifest["countries"].append({
            "countryCode": cc,
            "name": fm["country_name"],
            "flagEmoji": "",
            "cities": [],
            "defaultCity": "",
            "rulesFile": f"rules/{cc}_{pp}.json",
            "documentsFile": f"documents/{cc}.json",
        })
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# City research (new)
# ---------------------------------------------------------------------------

def build_city_prompt(fm: dict, action_step_schema: dict, existing_rules: list[dict], existing_docs: list[dict]) -> str:
    city = fm["city"]
    country_name = fm["country_name"]
    country_code = fm["country_code"]
    passport = fm["passport_country"]

    # Summarise existing rules so Claude knows which rules to attach steps to
    rule_summary = []
    for r in existing_rules:
        rule_summary.append(
            f"- \"{r['displayName']}\" (ruleType: {r['ruleType']}, "
            f"thresholdDays: {r['thresholdDays']})"
        )
    rules_list = "\n".join(rule_summary) if rule_summary else "(no rules on file)"

    # Summarise existing documents so Claude avoids duplicating them as action steps
    doc_names = [f"- \"{d['name']}\"" for d in existing_docs]
    docs_list = "\n".join(doc_names) if doc_names else "(none)"

    return f"""You are researching city-specific information for a travel-logging app. A user is travelling to **{city}, {country_name} ({country_code})** on a {passport} passport. The app already has country-level rules but needs location-specific action steps for this city.

EXISTING RULES for {country_name}:
{rules_list}

EXISTING DOCUMENTS (already shown separately in the app — do NOT duplicate as action steps):
{docs_list}

YOUR TASK: For each rule above where city-specific information is relevant, produce action steps for **{city}**. Focus on:

1. **Exit-Entry Administration / PSB office** — the local Public Security Bureau office that handles visa extensions, registration, and immigration matters. Find the official address, website URL, and operating hours.
2. **Tax bureau office** — if a CALENDAR_YEAR_THRESHOLD or CONSECUTIVE_YEAR_RULE exists, find the local tax authority office address and URL.
3. **Visa extension procedures** — any city-specific processes or offices for extending visas or changing status.
4. **Other relevant local offices** — e.g. foreign affairs office, notarial offices needed for document authentication.

INSTRUCTIONS:
- Use the web_search tool to find authoritative sources. Prefer .gov / government / official domains.
- Every `url` must point to an official source. If you cannot find an official URL, omit the `url` field.
- Set `location` to exactly "{city}" on every action step (case-sensitive match).
- Set `isOnline` to false for physical offices, true for online services.
- Include the full street address in the `address` field for physical offices.
- Do NOT invent addresses or URLs — if you cannot verify, omit that step entirely.
- Do NOT duplicate action steps that already exist. Only add NEW city-specific steps.
- Do NOT add action steps that cover the same topic as an existing document listed
  above. Documents handle universal entry paperwork; action steps handle local offices
  and procedures. Never put the same topic in both.

OUTPUT: Return exactly ONE JSON object (no prose, no markdown fences) with two keys:

  {{
    "city_steps": {{
      "<exact rule displayName>": [
        {{
          "title": "...",
          "description": "...",
          "url": "https://...",
          "location": "{city}",
          "address": "...",
          "isOnline": false
        }}
      ]
    }},
    "sources": [ {{ "title": "...", "url": "https://..." }}, ... ]
  }}

The keys in `city_steps` MUST match the exact `displayName` strings from the existing rules listed above. Only include rules where you found city-specific information — omit rules with no relevant local data.

ACTION STEP SCHEMA (each step must conform to this):
```json
{json.dumps(action_step_schema, indent=2)}
```
"""


def validate_city(payload: dict, action_step_schema: dict) -> list[str]:
    """Validate each action step in the city_steps payload."""
    errors = []
    city_steps = payload.get("city_steps", {})
    if not isinstance(city_steps, dict):
        return ["city_steps must be an object mapping rule displayName to arrays of action steps"]
    for rule_name, steps in city_steps.items():
        if not isinstance(steps, list):
            errors.append(f"city_steps[\"{rule_name}\"] must be an array")
            continue
        for i, step in enumerate(steps):
            for err in Draft7Validator(action_step_schema).iter_errors(step):
                errors.append(f"city_steps[\"{rule_name}\"][{i}]: {err.message}")
    return errors


def write_city_outputs(root: Path, fm: dict, payload: dict) -> None:
    cc, pp = fm["country_code"], fm["passport_country"]
    city = fm["city"]

    # Load existing rules
    rules_path = root / "rules" / f"{cc}_{pp}.json"
    if not rules_path.exists():
        die(f"Rules file {rules_path} does not exist. Country data must be added before city data.")
    existing_rules = json.loads(rules_path.read_text())

    # Merge city-specific action steps into existing rules
    city_steps = payload.get("city_steps", {})
    steps_added = 0
    for rule in existing_rules:
        display_name = rule["displayName"]
        if display_name in city_steps:
            new_steps = city_steps[display_name]
            existing_action_steps = rule.get("actionSteps", [])
            # Deduplicate: skip steps whose title+location already exist
            existing_keys = {
                (s.get("title", ""), s.get("location", ""))
                for s in existing_action_steps
            }
            for step in new_steps:
                key = (step.get("title", ""), step.get("location", ""))
                if key not in existing_keys:
                    existing_action_steps.append(step)
                    steps_added += 1
            rule["actionSteps"] = existing_action_steps

    if steps_added == 0:
        die(f"No new action steps were added for {city}. Model may have returned empty or duplicate data.")

    rules_path.write_text(json.dumps(existing_rules, indent=2, ensure_ascii=False) + "\n")

    # Add city to manifest if not already present
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for country in manifest["countries"]:
        if country["countryCode"] == cc:
            cities = country.get("cities", [])
            if city not in cities:
                cities.append(city)
                country["cities"] = cities
            break
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    print(f"Added {steps_added} action step(s) for {city}.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Common
# ---------------------------------------------------------------------------

def call_claude(client: Anthropic, prompt: str) -> str:
    """Call Claude with web-search enabled, return the text reply."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        messages=[{"role": "user", "content": prompt}],
    )
    # Collect all text blocks (web_search tool use blocks are interleaved).
    chunks = [block.text for block in resp.content if getattr(block, "type", None) == "text"]
    text = "\n".join(chunks).strip()
    if not text:
        die("Model returned no text content.")
    return text


def extract_json(text: str) -> dict:
    """Extract JSON from model output, handling interleaved reasoning text.

    With web_search enabled, Claude's text blocks may contain reasoning prose
    around the actual JSON object. We try several strategies:
    1. Parse the full text as JSON directly.
    2. Extract from markdown fences (```json ... ```).
    3. Find the outermost { ... } brace pair in the text.
    """
    cleaned = text.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 2: markdown fence
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n```", cleaned, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: find outermost { ... } by brace counting
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start:i + 1])
                    except json.JSONDecodeError:
                        break

    die(f"Model output is not valid JSON: could not extract JSON object\n\nFirst 500 chars:\n{text[:500]}")


def sources_markdown(payload: dict) -> str:
    sources = payload.get("sources", [])
    if not sources:
        return "_No sources returned by the model — review this PR carefully before merging._"
    lines = ["### Sources cited by Claude", ""]
    for s in sources:
        title = s.get("title", "(untitled)")
        url = s.get("url", "")
        lines.append(f"- [{title}]({url})")
    return "\n".join(lines)


def run_country(fm: dict, root: Path, client: Anthropic) -> None:
    """Full country research: rules + documents + manifest."""
    rule_schema, doc_schema, _ = load_schemas(root)
    prompt = build_country_prompt(fm, rule_schema, doc_schema)

    payload: dict | None = None
    errors: list[str] = []
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        text = call_claude(client, prompt)
        payload = extract_json(text)
        errors = validate_country(payload, rule_schema, doc_schema)
        if not errors:
            break
        if attempt < RETRY_ATTEMPTS:
            prompt = (
                build_country_prompt(fm, rule_schema, doc_schema)
                + "\n\nYour previous response had schema violations:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nFix them and return valid JSON."
            )
    if errors:
        die("Schema validation failed after retries:\n" + "\n".join(errors))
    assert payload is not None

    write_country_outputs(root, fm, payload)
    print(sources_markdown(payload))


def run_city(fm: dict, root: Path, client: Anthropic) -> None:
    """City research: add action steps to existing country rules."""
    cc, pp = fm["country_code"], fm["passport_country"]
    rules_path = root / "rules" / f"{cc}_{pp}.json"
    if not rules_path.exists():
        die(f"Cannot add city data: {rules_path} does not exist. Add the country first.")

    existing_rules = json.loads(rules_path.read_text())
    rule_schema, _, _ = load_schemas(root)
    # Extract the ActionStep schema from the rule schema definitions
    action_step_schema = rule_schema.get("definitions", {}).get("ActionStep", {})

    # Load existing documents so the prompt can instruct Claude to avoid duplicating them
    docs_path = root / "documents" / f"{cc}.json"
    existing_docs = json.loads(docs_path.read_text()) if docs_path.exists() else []

    prompt = build_city_prompt(fm, action_step_schema, existing_rules, existing_docs)

    payload: dict | None = None
    errors: list[str] = []
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        text = call_claude(client, prompt)
        payload = extract_json(text)
        errors = validate_city(payload, action_step_schema)
        if not errors:
            break
        if attempt < RETRY_ATTEMPTS:
            prompt = (
                build_city_prompt(fm, action_step_schema, existing_rules, existing_docs)
                + "\n\nYour previous response had schema violations:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nFix them and return valid JSON."
            )
    if errors:
        die("Schema validation failed after retries:\n" + "\n".join(errors))
    assert payload is not None

    write_city_outputs(root, fm, payload)
    print(sources_markdown(payload))


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        die("ANTHROPIC_API_KEY is not set.")
    body_path = os.environ.get("ISSUE_BODY_PATH")
    if not body_path:
        die("ISSUE_BODY_PATH is not set.")
    body = Path(body_path).read_text()
    fm = parse_frontmatter(body)

    root = Path(os.environ.get("REPO_ROOT", "."))
    client = Anthropic(api_key=api_key)

    if fm["request_type"] == "missing_country":
        run_country(fm, root, client)
    elif fm["request_type"] == "missing_city":
        run_city(fm, root, client)
    else:
        print(f"Skipping: request_type={fm['request_type']} not supported.")
        sys.exit(0)


if __name__ == "__main__":
    main()
