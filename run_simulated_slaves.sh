#!/usr/bin/env bash
# Script to simulate multiple slaves on a single machine for testing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COUNT="${1:-3}"
MASTER_URL="${2:-${MASTER_URL:-http://127.0.0.1:8000}}"

if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

echo "=========================================================="
echo "  🚀 Launching $COUNT simulated slaves targeting $MASTER_URL"
echo "=========================================================="

PIDS=()

cleanup() {
    echo ""
    echo "Stopping all simulated slaves..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait
    echo "All slaves stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

for i in $(seq 1 "$COUNT"); do
    WORKER_ID="sim-slave-$i"
    echo "Starting slave $i ($WORKER_ID)..."
    python -m slave.main --master "$MASTER_URL" --worker-id "$WORKER_ID" &
    PIDS+=($!)
    sleep 0.2
done

echo "All $COUNT slaves running in background."
echo "Press Ctrl+C to stop all slaves."

wait
