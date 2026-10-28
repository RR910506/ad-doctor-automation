"""Password-gated Gemini API workspace; deploy separately from the public demo."""
import hmac
import os
import time
import streamlit as st
from src.services.gemini_workspace import GeminiWorkspace
from ui.pages.gemini_workspace import render
from ui.theme import apply_theme

st.set_page_config(page_title='Ad Doctor · Gemini', page_icon='🩺', layout='wide')
apply_theme(st)


def configured(name):
    value = os.environ.get(name, '')
    if value:
        return value
    try:
        return str(st.secrets.get(name, ''))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return ''


password = configured('AD_DOCTOR_ACCESS_PASSWORD')
if len(password) < 16:
    st.info('The owner must configure AD_DOCTOR_ACCESS_PASSWORD (at least 16 characters) in Streamlit Secrets before this app can open.')
    st.stop()
authenticated = hmac.compare_digest(str(st.session_state.get('_access_proof', '')).encode(), password.encode())
if not authenticated:
    st.title('Ad Doctor team access')
    st.caption('Enter the workspace password supplied by the owner, not your Google password.')
    with st.form('access'):
        submitted_password = st.text_input('Workspace password', type='password')
        submitted = st.form_submit_button('Open workspace')
    if submitted:
        if time.monotonic() < st.session_state.get('_access_retry', 0):
            st.error('Wait a few seconds before trying again.')
        elif hmac.compare_digest(submitted_password.encode(), password.encode()):
            st.session_state['_access_proof'] = password
            st.rerun()
        else:
            st.session_state['_access_retry'] = time.monotonic() + 5
            st.error('Incorrect workspace password.')
    st.stop()

if st.sidebar.button('Sign out'):
    st.session_state.clear()
    st.rerun()
key = configured('GEMINI_API_KEY')
if not key:
    st.info('Add GEMINI_API_KEY in Streamlit Secrets, then reboot the app.')
    st.stop()
with st.sidebar:
    st.markdown('<div class="ad-kicker">THE AD DOCTOR</div><h2>Content Intelligence</h2>', unsafe_allow_html=True)
    st.caption('Today')
    st.caption('My content')
    with st.expander('Expert workspace'):
        st.caption('AI models, evidence and collection reports appear in the main workspace when needed.')
    st.divider()
    st.caption('Marketing examined. Strategy diagnosed.')
render(st, GeminiWorkspace(api_key=key))
