#!/usr/bin/env bash
# Exit 0 after $IDLE_MINUTES with no llama-server activity.
# Activity = llama-server's Prometheus counter of predicted tokens advancing.
# When this script exits, the SkyPilot run block finishes; combined with
# `sky launch --idle-minutes-to-autostop N --down`, the cluster terminates.
set -euo pipefail

: "${IDLE_MINUTES:=15}"

METRIC="llamacpp:n_tokens_predicted_total"
POLL_SECONDS=60
IDLE_SECONDS=$((IDLE_MINUTES * 60))

read_counter() {
  curl -sf http://localhost:8080/metrics 2>/dev/null \
    | awk -v m="$METRIC" '$1 == m { print $2; exit }'
}

last_count=$(read_counter || echo "")
last_activity=$(date +%s)

echo "[idle-watch] IDLE_MINUTES=${IDLE_MINUTES}, polling every ${POLL_SECONDS}s"

while true; do
  sleep "$POLL_SECONDS"
  current=$(read_counter || echo "")

  if [[ -n "$current" && "$current" != "$last_count" ]]; then
    last_count="$current"
    last_activity=$(date +%s)
    continue
  fi

  now=$(date +%s)
  elapsed=$((now - last_activity))
  if (( elapsed >= IDLE_SECONDS )); then
    echo "[idle-watch] idle for ${elapsed}s (>= ${IDLE_SECONDS}s), exiting"
    exit 0
  fi
done
