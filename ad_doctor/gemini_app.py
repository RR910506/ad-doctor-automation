"""Compatibility entrypoint for the existing Streamlit URL.

Ad Doctor itself is independent of the AI provider selected in its settings.
New deployments can use ad_doctor_app.py; existing links need no change.
"""
from ui.team_app import main

main()
