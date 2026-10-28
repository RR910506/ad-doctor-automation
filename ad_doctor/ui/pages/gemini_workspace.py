"""Real-input Gemini interface. Calls are delegated to a service."""
import json
import hashlib
import tempfile
from pathlib import Path
from src.services.publication_collection import collect_publications
from src.adapters.gemini_research_provider import GeminiProviderError
from ui.components import page_header


def render(st, workspace):
    page_header(st, 'Today', 'See what Ad Doctor collected, research qualified stories, and review prepared content.')
    collection = st.session_state.get('publication_collection')
    research = st.session_state.get('gemini_research')
    draft = st.session_state.get('gemini_draft')
    first, second, third = st.columns(3)
    first.metric('Publications checked', (collection or {}).get('collection', {}).get('sources_checked', 0))
    second.metric('Topics ready to research', len((collection or {}).get('topics', [])))
    third.metric('Waiting for your review', 1 if draft else 0)
    st.caption('Approved publications → collected stories → research → content for your approval.')

    st.subheader('Collect today’s stories')
    st.write('Ad Doctor checks the approved publication list and only offers a topic when three distinct publisher groups cover the same development.')
    if st.button('Collect approved publications', type='primary'):
        if 'collection_database' not in st.session_state:
            st.session_state['collection_database'] = str(Path(tempfile.mkdtemp(prefix='ad-doctor-')) / 'collection.db')
        try:
            with st.spinner('Collecting full articles and comparing coverage. This can take several minutes…'):
                st.session_state['publication_collection'] = collect_publications(st.session_state['collection_database'])
        except Exception:
            st.error('Collection failed. Check server logs and source access before retrying.')
    collection = st.session_state.get('publication_collection')
    if collection:
        count = len(collection['topics'])
        if count:
            st.success(f'{count} topic(s) have usable article text from at least three publisher groups.')
            for candidate in collection['topics']:
                with st.container(border=True):
                    st.write(f"**{candidate['topic']}**")
                    st.caption(f"Evidence from {candidate['publications']} distinct publisher groups")
        else:
            st.info('No story has reached the three-publication requirement in this scan. The collection report explains any access gaps.')
        with st.expander('Expert details · collection report'):
            st.caption(collection['limitations'])
            st.json(collection['collection'])
            st.download_button('Download collection report', json.dumps(collection, indent=2), 'collection-report.json')

    st.subheader('Prepare content')
    st.write('Research and writing are separate. Research receives collected evidence; writing starts only after you review that research.')
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
    with st.expander('Expert details · AI access and cost'):
        st.caption('A listed model is not necessarily free. Confirm your project’s API tier and limits in Google AI Studio. Calls may consume quota or incur charges on a billed project. No automatic retries or fallback calls are made.')
    research_model = st.selectbox('Research model', models)
    candidates = collection['topics'] if collection else []
    if candidates:
        index = st.selectbox('Collected topic', range(len(candidates)), format_func=lambda i: candidates[i]['topic'])
        topic, material = candidates[index]['topic'], candidates[index]['material']
        qualified = True
        with st.expander('Expert details · source evidence supplied to Gemini'):
            st.text(material)
    else:
        st.info('Collect publications to populate topics automatically.')
        with st.expander('Expert diagnostics · manual input'):
            st.caption('Use this only to test the AI connection. Normal operation selects topics and evidence automatically.')
            topic = st.text_input('Exact marketing topic', max_chars=500)
            material = st.text_area('Paste real article excerpts with publication names and source URLs', height=200, max_chars=40000)
            qualified = st.checkbox('I confirm this exact topic is covered by at least three distinct approved publications')
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
    with st.container(border=True):
        st.write(research['text'])
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
        st.subheader('My content')
        article_tab, expert_tab = st.tabs(['Article', 'Expert details'])
        with article_tab:
            with st.container(border=True):
                st.caption('DRAFT · PENDING YOUR REVIEW')
                st.write(draft['text'])
                st.download_button('Download draft', json.dumps(draft, indent=2), 'gemini-draft.json', 'application/json')
        with expert_tab:
            st.json({'research': research, 'draft': draft})
    st.caption('Results live in this browser session. Download them before closing. Nothing is published or emailed without human approval.')
