#!/usr/bin/env bash
set -e

# ==============================================================================
# Distributed API Load Tester - Slave Quick Setup & Run Script
# ==============================================================================

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================="
echo "  🚀 NTUT Exam Load Testing System - Slave Worker Setup"
echo "=========================================================="

# 1. Resolve Master URL (Argument 1 > Environment Variable > Default)
MASTER_URL="${1:-${MASTER_URL:-http://127.0.0.1:8000}}"

echo "[1/4] Checking Python environment..."
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "❌ Error: Python 3 is not installed or not in PATH."
    exit 1
fi

PY_VERSION=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "      Found Python version: $PY_VERSION ($PYTHON_CMD)"

# 2. Virtual Environment Setup
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "[2/4] Creating virtual environment (.venv)..."
    $PYTHON_CMD -m venv "$VENV_DIR"
else
    echo "[2/4] Virtual environment already exists."
fi

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# 3. Install Dependencies
echo "[3/4] Installing / Updating dependencies from requirements.txt..."
pip install --upgrade pip --quiet
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

# 4. Launch Slave Worker
echo "[4/4] Starting Slave Worker connected to Master: $MASTER_URL"
echo "      (Press Ctrl+C to terminate the worker at any time)"
echo "----------------------------------------------------------"

exec python -m slave.main --master "$MASTER_URL"
