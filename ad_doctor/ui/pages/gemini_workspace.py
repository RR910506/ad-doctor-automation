"""Real-input Gemini interface. Calls are delegated to a service."""
import json
import hashlib
from src.adapters.gemini_research_provider import GeminiProviderError


def render(st, workspace):
    st.title('Your Gemini workspace')
    st.write('Bring real source excerpts, research the topic, then prepare a draft.')
    st.caption('No sample content. Research analyzes your supplied text; URL browsing and automatic collection are not enabled in this workspace.')
    if st.button('Check connection and load available models'):
        try:
            st.session_state['gemini_available_models'] = workspace.available_models()
            st.success('Google accepted the model-list request. A generation call is still needed to verify inference access.')
        except GeminiProviderError as exc:
            st.error(str(exc))
    models = st.session_state.get('gemini_available_models', [])
    if not models:
        st.info('Check the connection first. If no models are returned, check API permissions and regional availability.')
        return
    st.caption('A listed model is not necessarily free. Confirm your project’s API tier and limits in Google AI Studio. Calls may consume quota or incur charges on a billed project. No automatic retries or fallback calls are made.')
    research_model = st.selectbox('Research model', models)
    topic = st.text_input('Exact marketing topic', max_chars=500)
    material = st.text_area('Paste real article excerpts with publication names and source URLs', height=200, max_chars=40000)
    qualified = st.checkbox('I confirm this exact topic is covered by at least three distinct approved publications')
    st.caption('This confirmation is your manual qualification check. The existing automated qualification pipeline is not connected to this workspace yet.')
    identity = hashlib.sha256(json.dumps([topic, material, research_model]).encode()).hexdigest()
    if st.button('Research with Gemini', disabled=not(topic.strip() and material.strip() and qualified)):
        if st.session_state.get('gemini_research_id') == identity:
            st.info('Showing the existing result for this input. Change the input or model for another experiment.')
        else:
            try:
                with st.spinner('Analyzing your source material…'):
                    result = workspace.run(stage='Research', model=research_model, topic=topic, material=material)
                st.session_state['gemini_research'] = result
                st.session_state['gemini_research_id'] = identity
                st.session_state.pop('gemini_draft', None)
                st.session_state.pop('gemini_draft_id', None)
            except (GeminiProviderError, ValueError) as exc:
                st.error(str(exc))
    research = st.session_state.get('gemini_research')
    if not research or st.session_state.get('gemini_research_id') != identity or not qualified:
        return
    st.subheader('Research to review')
    st.text(research['text'])
    st.download_button('Download research', json.dumps(research, indent=2), 'gemini-research.json', 'application/json')
    st.warning('Check the cited articles and remove unsupported claims before drafting. This memo is not a validated Evidence Pack.')
    reviewed_material = st.text_area('Research approved by you for drafting (edit as needed)', value=research['text'], key=f'review_{identity}', max_chars=40000, height=200)
    reviewed = st.checkbox('I checked the source citations and reviewed the facts', key=f'checked_{identity}')
    writing_model = st.selectbox('Writing model', models)
    draft_id = hashlib.sha256(json.dumps([identity, reviewed_material, writing_model]).encode()).hexdigest()
    if st.button('Create draft with Gemini', disabled=not reviewed or not reviewed_material.strip()):
        if st.session_state.get('gemini_draft_id') == draft_id:
            st.info('Showing the existing draft for these inputs.')
        else:
            try:
                with st.spinner('Preparing your draft…'):
                    result = workspace.run(stage='Writing', model=writing_model, topic=topic, material=reviewed_material, reviewed=reviewed)
                st.session_state['gemini_draft'] = result
                st.session_state['gemini_draft_id'] = draft_id
            except (GeminiProviderError, ValueError) as exc:
                st.error(str(exc))
    draft = st.session_state.get('gemini_draft')
    if draft and st.session_state.get('gemini_draft_id') == draft_id:
        st.subheader('Draft — pending review')
        st.text(draft['text'])
        st.download_button('Download draft', json.dumps(draft, indent=2), 'gemini-draft.json', 'application/json')
    st.caption('Results live in this browser session. Download them before closing. Nothing is published, emailed or added to the operational database.')
