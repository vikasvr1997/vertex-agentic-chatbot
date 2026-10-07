#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ID="premium-odyssey-508202-k9"
LOCATION="us-east1"
RESERVATION_NAME="streamlit-gql-session-$(date -u +%Y%m%d%H%M%S)-$$"
RESERVATION_ID="${PROJECT_ID}:${LOCATION}.${RESERVATION_NAME}"
RESERVATION_CREATED=false
ASSIGNMENT_ATTEMPTED=false

for command_name in bq jq; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command_name" >&2
        exit 1
    fi
done

if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    printf 'Project virtualenv not found at %s/.venv\n' "$PROJECT_ROOT" >&2
    exit 1
fi

cleanup() {
    local exit_code=$?
    local assignment_ids
    trap - EXIT INT TERM
    set +e

    if [[ "$RESERVATION_CREATED" == true ]]; then
        if [[ "$ASSIGNMENT_ATTEMPTED" == true ]]; then
            assignment_ids="$(
                bq ls --reservation_assignment \
                    --project_id="$PROJECT_ID" \
                    --location="$LOCATION" \
                    --format=prettyjson \
                    "$RESERVATION_ID" 2>/dev/null |
                    jq -r '.[]? | .name? | split("/")[-1]' 2>/dev/null
            )"
            while IFS= read -r assignment_id; do
                [[ -z "$assignment_id" ]] && continue
                bq rm --force --reservation_assignment \
                    --project_id="$PROJECT_ID" \
                    --location="$LOCATION" \
                    "$assignment_id"
            done <<< "$assignment_ids"
        fi

        bq rm --force --reservation \
                --project_id="$PROJECT_ID" \
                --location="$LOCATION" \
                "$RESERVATION_NAME"
    fi
    exit "$exit_code"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

RESERVATION_CREATED=true
bq mk --reservation \
    --project_id="$PROJECT_ID" \
    --location="$LOCATION" \
    --edition=ENTERPRISE \
    --slots=0 \
    --autoscale_max_slots=50 \
    "$RESERVATION_NAME"

ASSIGNMENT_ATTEMPTED=true
bq mk --reservation_assignment \
    --project_id="$PROJECT_ID" \
    --location="$LOCATION" \
    --reservation_id="$RESERVATION_ID" \
    --job_type=QUERY \
    --assignee_type=PROJECT \
    --assignee_id="$PROJECT_ID"
printf 'Enterprise reservation %s is assigned to project %s in %s.\n' \
    "$RESERVATION_NAME" "$PROJECT_ID" "$LOCATION"
printf 'Streamlit is managed by this wrapper; press Ctrl+C to stop it and clean up the reservation.\n'

cd "$PROJECT_ROOT"
"$PROJECT_ROOT/.venv/bin/python" -m streamlit run \
    src/agentic_chatbot/frontend/streamlit_app.py \
    --server.port 8501
