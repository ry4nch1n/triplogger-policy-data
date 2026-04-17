#!/usr/bin/env bash
# Process a single `missing-country` or `missing-city` issue:
# research, validate, commit directly to main, close issue with summary.
#
# Required env:
#   GH_TOKEN, ANTHROPIC_API_KEY  — credentials
#   ISSUE_NUMBER                 — issue to process
#   GITHUB_SERVER_URL, GITHUB_REPOSITORY, GITHUB_RUN_ID — for log links in failure comments
#
# Exits 0 whether the research succeeded or was gracefully skipped; only exits
# non-zero on unrecoverable environment problems (e.g. bad issue body).

set -euo pipefail

N="${ISSUE_NUMBER:?ISSUE_NUMBER is required}"

echo "::group::Issue #$N"

# Skip if issue is already closed — avoids reprocessing on scheduled runs.
ISSUE_STATE=$(gh issue view "$N" --json state -q .state)
if [ "$ISSUE_STATE" = "CLOSED" ]; then
  echo "Issue #$N is already closed — skipping."
  echo "::endgroup::"
  exit 0
fi

gh issue view "$N" --json body -q .body > /tmp/issue-body.md
REQUEST_TYPE=$(awk '/^request_type:/{print $2; exit}' /tmp/issue-body.md)
COUNTRY_CODE=$(awk '/^country_code:/{print $2; exit}' /tmp/issue-body.md)
COUNTRY_NAME=$(awk '/^country_name:/{sub(/^country_name:[[:space:]]*/, ""); print; exit}' /tmp/issue-body.md)
CITY=$(awk '/^city:/{sub(/^city:[[:space:]]*/, ""); print; exit}' /tmp/issue-body.md)

if [ -z "$COUNTRY_CODE" ] || [ -z "$COUNTRY_NAME" ]; then
  echo "Issue #$N frontmatter missing country_code or country_name — skipping."
  gh issue comment "$N" --body "Auto-research skipped: issue body is missing the required YAML frontmatter fields (country_code, country_name)."
  echo "::endgroup::"
  exit 0
fi

# For city requests, the country's rules file must already exist.
if [ "$REQUEST_TYPE" = "missing_city" ]; then
  if [ -z "$CITY" ]; then
    echo "Issue #$N is missing_city but has no city field — skipping."
    gh issue comment "$N" --body "Auto-research skipped: missing_city issue requires a \`city\` field in the YAML frontmatter."
    echo "::endgroup::"
    exit 0
  fi
  if [ ! -f "rules/${COUNTRY_CODE}_SG.json" ]; then
    echo "Issue #$N: country data for ${COUNTRY_CODE} does not exist yet — skipping city."
    gh issue comment "$N" --body "Auto-research skipped: country data for **${COUNTRY_NAME}** (\`${COUNTRY_CODE}\`) must exist before adding city-specific data. Please file a \`missing-country\` issue first."
    echo "::endgroup::"
    exit 0
  fi
fi

# Run research script; capture stdout for sources section
if ! ISSUE_BODY_PATH=/tmp/issue-body.md REPO_ROOT=. \
     python3 .github/scripts/research.py > /tmp/sources.md 2>/tmp/research.err; then
  echo "Research failed for issue #$N"
  cat /tmp/research.err
  ERR_TAIL=$(tail -20 /tmp/research.err | tr '\n' ' ' | cut -c1-500)
  RUN_URL="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
  gh issue comment "$N" --body "Auto-research failed. See [workflow logs]($RUN_URL). Last error: \`$ERR_TAIL\`"
  echo "::endgroup::"
  exit 0
fi

# Schema validation as safety net before commit.
check-jsonschema --schemafile schema/rule.schema.json "rules/${COUNTRY_CODE}_SG.json"
check-jsonschema --schemafile schema/manifest.schema.json manifest.json

if [ "$REQUEST_TYPE" = "missing_country" ]; then
  check-jsonschema --schemafile schema/document.schema.json "documents/${COUNTRY_CODE}.json"
  git add manifest.json "rules/${COUNTRY_CODE}_SG.json" "documents/${COUNTRY_CODE}.json"
else
  git add manifest.json "rules/${COUNTRY_CODE}_SG.json"
fi

# Build commit message
COMMIT_MSG_FILE=$(mktemp)
if [ "$REQUEST_TYPE" = "missing_city" ]; then
  WHAT="city data: ${CITY}, ${COUNTRY_NAME} (${COUNTRY_CODE})"
else
  WHAT="country data: ${COUNTRY_NAME} (${COUNTRY_CODE})"
fi
{
  echo "Auto-research: add ${WHAT}"
  echo ""
  echo "Closes #${N}. Schema-validated by research.py + check-jsonschema."
} > "$COMMIT_MSG_FILE"

# Commit and push directly to main
git commit -F "$COMMIT_MSG_FILE"
rm -f "$COMMIT_MSG_FILE"
COMMIT_SHA=$(git rev-parse --short HEAD)
git push origin main

# Build summary comment for the issue (serves as email notification)
COMMENT_FILE=$(mktemp)
{
  echo "## Auto-research complete"
  echo ""
  if [ "$REQUEST_TYPE" = "missing_city" ]; then
    echo "Added city-specific action steps for **${CITY}** in **${COUNTRY_NAME}** (\`${COUNTRY_CODE}\`)."
  else
    echo "Added policy data for **${COUNTRY_NAME}** (\`${COUNTRY_CODE}\`)."
  fi
  echo ""
  echo "Commit: [\`${COMMIT_SHA}\`](${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/commit/${COMMIT_SHA})"
  echo ""
  echo "### Spot-check"
  if [ "$REQUEST_TYPE" = "missing_city" ]; then
    echo "- Office addresses are current"
    echo "- URLs point to official .gov sources"
    echo "- \`location\` field is exactly \`${CITY}\`"
  else
    echo "- \`thresholdDays\` / \`windowDays\` match cited sources"
    echo "- \`actionStep.url\` values point to official sources"
    echo "- Tax residency day counts are correct"
  fi
  echo ""
  cat /tmp/sources.md
  echo ""
  echo "---"
  echo "_If data looks wrong, revert commit \`${COMMIT_SHA}\` and reopen this issue._"
  echo ""
  echo "_Data is live — your app will pick it up on next policy refresh._"
} > "$COMMENT_FILE"

gh issue close "$N" --comment "$(cat "$COMMENT_FILE")"
rm -f "$COMMENT_FILE"

echo "::endgroup::"
