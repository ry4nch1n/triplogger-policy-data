#!/usr/bin/env python3
"""Research a `missing-country` issue and produce rule/document JSON files.

Inputs (env vars):
  ANTHROPIC_API_KEY   — required, set via GitHub Actions secret.
  ISSUE_NUMBER        — GitHub issue number to research.
  ISSUE_BODY_PATH     — path to a file containing the full issue body.
  REPO_ROOT           — checkout root (defaults to cwd).

Outputs:
  Writes rules/<CC>_<PP>.json and documents/<CC>.json.
  Updates manifest.json to include the new country.
  Prints a Markdown "sources" block on stdout so the caller can include it in the PR body.

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
    for key in ("request_type", "country_code", "country_name", "passport_country"):
        if key not in data:
            die(f"Frontmatter missing required field: {key}")
    return data


def load_schemas(root: Path) -> tuple[dict, dict, dict]:
    return (
        json.loads((root / "schema" / "rule.schema.json").read_text()),
        json.loads((root / "schema" / "document.schema.json").read_text()),
        json.loads((root / "schema" / "manifest.schema.json").read_text()),
    )


def build_prompt(fm: dict, rule_schema: dict, doc_schema: dict) -> str:
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
    """Strip markdown fences if present, parse JSON."""
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        die(f"Model output is not valid JSON: {exc}\n\nFirst 500 chars:\n{text[:500]}")


def validate(payload: dict, rule_schema: dict, doc_schema: dict) -> list[str]:
    errors = []
    for err in Draft7Validator(rule_schema).iter_errors(payload.get("rules", [])):
        errors.append(f"rules: {err.message} at {list(err.path)}")
    for err in Draft7Validator(doc_schema).iter_errors(payload.get("documents", [])):
        errors.append(f"documents: {err.message} at {list(err.path)}")
    return errors


def write_outputs(root: Path, fm: dict, payload: dict) -> None:
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


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        die("ANTHROPIC_API_KEY is not set.")
    body_path = os.environ.get("ISSUE_BODY_PATH")
    if not body_path:
        die("ISSUE_BODY_PATH is not set.")
    body = Path(body_path).read_text()
    fm = parse_frontmatter(body)

    if fm["request_type"] != "missing_country":
        print(f"Skipping: request_type={fm['request_type']} not yet supported.")
        sys.exit(0)

    root = Path(os.environ.get("REPO_ROOT", "."))
    rule_schema, doc_schema, _manifest_schema = load_schemas(root)

    client = Anthropic(api_key=api_key)
    prompt = build_prompt(fm, rule_schema, doc_schema)

    payload: dict | None = None
    errors: list[str] = []
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        text = call_claude(client, prompt)
        payload = extract_json(text)
        errors = validate(payload, rule_schema, doc_schema)
        if not errors:
            break
        if attempt < RETRY_ATTEMPTS:
            prompt = (
                build_prompt(fm, rule_schema, doc_schema)
                + "\n\nYour previous response had schema violations:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nFix them and return valid JSON."
            )
    if errors:
        die("Schema validation failed after retries:\n" + "\n".join(errors))
    assert payload is not None

    write_outputs(root, fm, payload)
    print(sources_markdown(payload))


if __name__ == "__main__":
    main()
