#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check required env vars
: "${AZURE_AI_SEARCH_ENDPOINT:?Set AZURE_AI_SEARCH_ENDPOINT}"
: "${AZURE_AI_SEARCH_API_KEY:?Set AZURE_AI_SEARCH_API_KEY}"

export AZURE_AI_SEARCH_INDEX_NAME="${AZURE_AI_SEARCH_INDEX_NAME:-test-search-omission}"

SYSTEM_NAME="${SYSTEM_NAME:-product}"
DOC_COUNT="${DOC_COUNT:-10000}"
ITERATIONS="${ITERATIONS:-3}"
PREV_VERSION="${PREV_VERSION:-20260414}"
CURR_VERSION="${CURR_VERSION:-20260415}"

mkdir -p results

echo "============================================"
echo " Azure AI Search Omission Test"
echo "============================================"
echo "Endpoint:    $AZURE_AI_SEARCH_ENDPOINT"
echo "Index:       $AZURE_AI_SEARCH_INDEX_NAME"
echo "System:      $SYSTEM_NAME"
echo "Doc count:   $DOC_COUNT"
echo "Iterations:  $ITERATIONS"
echo "Versions:    $PREV_VERSION -> $CURR_VERSION"
echo "============================================"
echo ""

echo "[1/3] Running Test 1: Customer workflow reproduction..."
python3 test1_workflow.py \
    --system-name "$SYSTEM_NAME" \
    --prev-version "$PREV_VERSION" \
    --current-version "$CURR_VERSION" \
    --count "$DOC_COUNT" \
    --iterations "$ITERATIONS" \
    --output results/test1_workflow.json

echo ""
echo "[2/3] Running Test 2: Count consistency verification..."
python3 test2_workflow.py \
    --system-name "$SYSTEM_NAME" \
    --version "$PREV_VERSION" \
    --count "$DOC_COUNT" \
    --check-interval 2 \
    --consecutive 3 \
    --iterations "$ITERATIONS" \
    --output results/test2_workflow.json

echo ""
echo "[3/3] Running Test 3: Pagination accuracy verification..."
python3 test3_pagination.py \
    --system-name "$SYSTEM_NAME" \
    --old-version "$PREV_VERSION" \
    --new-version "$CURR_VERSION" \
    --count "$DOC_COUNT" \
    --iterations "$ITERATIONS" \
    --output results/test3_pagination.json

echo ""
echo "============================================"
echo " All tests complete. Results in results/"
echo "============================================"
