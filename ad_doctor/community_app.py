"""Public, read-only interface demo for Streamlit Community Cloud.

No database, provider keys, scheduler, publishing or email connection is needed.
"""
from ui.app_shell import render_app

render_app(community_demo=True)
