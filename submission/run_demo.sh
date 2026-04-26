#!/usr/bin/env bash
# Launch the HydroHack Streamlit GUI demo (Linux/macOS).
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo ">>> creating .venv (one-time setup)"
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

echo ">>> starting Streamlit on http://localhost:8501"
.venv/bin/streamlit run app.py
