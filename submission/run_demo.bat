@echo off
REM Launch the HydroHack Streamlit GUI demo (Windows).
cd /d "%~dp0"

if not exist .venv (
    echo ^>^>^> creating .venv ^(one-time setup^)
    python -m venv .venv
    .venv\Scripts\pip install --upgrade pip
    .venv\Scripts\pip install -r requirements.txt
)

echo ^>^>^> starting Streamlit on http://localhost:8501
.venv\Scripts\streamlit run app.py
