#!/usr/bin/env bash
# Cron-able budget guard. Runs locally (not on the GPU).
# If `sky cost-report` total for this month exceeds $MONTHLY_BUDGET_USD,
# tears down the cluster.
#
# NOTE: sky cost-report is SkyPilot's local accounting — provider-side billing
# is the source of truth. Also set a RunPod spend limit at
#   https://www.runpod.io/console/user/billing
# as the real backstop.
#
# Example crontab (check every 15 min):
#   */15 * * * * cd /path/to/skypilot-llms && bash scripts/budget-check.sh >> /tmp/skypilot-budget.log 2>&1
set -euo pipefail

# Source .env for MONTHLY_BUDGET_USD
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
if [[ -f "$REPO_DIR/.env" ]]; then
  set -a; source "$REPO_DIR/.env"; set +a
fi

: "${MONTHLY_BUDGET_USD:?MONTHLY_BUDGET_USD not set in .env}"
CLUSTER="${CLUSTER:-llm-cpp}"

# Parse "TOTAL" row from sky cost-report. Format has varied between SkyPilot
# versions, so we fall back to grepping any $N.NN number on a TOTAL-ish line.
total=$(sky cost-report 2>/dev/null \
  | awk 'tolower($0) ~ /total/ { for (i=1; i<=NF; i++) if ($i ~ /^\$?[0-9.]+$/) { gsub(/\$/,"",$i); print $i; exit } }' \
  || echo "0")

total="${total:-0}"
echo "[budget-check] this month so far: \$${total} / \$${MONTHLY_BUDGET_USD} cap"

if awk "BEGIN { exit !(${total} > ${MONTHLY_BUDGET_USD}) }"; then
  echo "[budget-check] OVER BUDGET — tearing down $CLUSTER"
  sky down -y "$CLUSTER" || true
  exit 1
fi
