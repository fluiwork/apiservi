#!/bin/sh
set -eu

echo "=== STARTUP DIAGNOSTICS ==="
echo "Date: $(date -u)"
echo "User: $(whoami 2>/dev/null || echo unknown)"
echo "PWD: $(pwd)"
echo "ENV VARS (showing PORT and MAX_CONCURRENT_SCRAPES):"
echo "  PORT=${PORT:-<not-set>}"
echo "  MAX_CONCURRENT_SCRAPES=${MAX_CONCURRENT_SCRAPES:-<not-set>}"
echo "  ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-<not-set>}"
echo "---------------------------------"
echo "Python version:"
python --version || true
echo "Pip list (first 200 lines):"
pip list --format=columns | sed -n '1,200p' || true
echo "---------------------------------"
echo "Try importing app module to capture startup traceback (if any):"
python - <<'PY'
import sys, traceback
try:
    import app
    print("IMPORT OK: module 'app' imported successfully.")
except Exception:
    traceback.print_exc()
    sys.exit(1)
PY
echo "---------------------------------"
echo "Starting Gunicorn..."
exec /opt/venv/bin/gunicorn -w 1 -k gthread --threads 4 --timeout 120 -b 0.0.0.0:$PORT app:app --log-level debug
