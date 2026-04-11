@echo off
cd /d C:\Watkins

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate
pip install -r requirements.txt --quiet
streamlit run app.py
