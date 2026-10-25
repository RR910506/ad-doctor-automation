import json
from datetime import datetime, timezone
from src.model_lab import TOOLS, make_brief, comparison_id
from ui.components import page_header


def render(st, service, actions):
    page_header(st, 'AI tools & comparisons', 'Try the same brief in different tools, then compare the answers.')
    st.info('Manual mode: open the chosen tool in your own account and paste its answer here. No account is connected and no paid API is called.')
    role = st.radio('What are you testing?', ('Research', 'Writing'), horizontal=True)
    catalog = {x['name']: x for x in TOOLS if role in x['roles']}
    chosen = st.selectbox('Tool', list(catalog))
    tool = catalog[chosen]
    st.write(tool['purpose'])
    st.caption(tool['access'])
    st.caption('Collection is separate: Ad Doctor retrieves approved publication pages using HTTP and optional browser rendering. A language model is not required for collection.')
    topic = st.text_input('Topic to investigate')
    evidence = st.text_area('Approved links and extracted text' if role == 'Research' else 'Reviewed evidence for this writing test', height=160)
    reviewed = role == 'Research' or st.checkbox('I have reviewed this evidence before testing a writing tool')
    if not topic.strip() or not evidence.strip() or not reviewed:
        st.caption('Add the topic and source material to prepare a brief.')
        return
    brief = make_brief(role, topic, evidence)
    identity = comparison_id(role, topic, evidence)
    with st.expander('Copy your brief', expanded=True):
        st.code(brief, language=None)
    st.link_button(f'Open {chosen}', tool['url'])
    st.download_button('Download brief', brief, 'ad-doctor-brief.txt')
    st.warning('Only paste material you are authorized to share with that provider. Returned answers remain unverified and cannot approve or publish content.')
    with st.form(f'model_result_{identity}_{chosen}'):
        model = st.text_input('Exact model shown by the tool', placeholder='Enter “unknown” if the website does not show it')
        answer = st.text_area('Paste the answer', height=180)
        notes = st.text_area('Your review: supported claims, missing citations, invented details')
        score = st.slider('Your quality rating (subjective)', 1, 5, 3)
        submitted = st.form_submit_button('Add to this comparison')
    if submitted:
        if not answer.strip() or not model.strip():
            st.error('Add the answer and model name (or unknown).')
        else:
            record = dict(brief_id=identity, stage=role, topic=topic, brief=brief, tool=chosen,
                          model=model, answer=answer, reviewer_notes=notes, rating=score,
                          status='unverified_manual_result', recorded_at=datetime.now(timezone.utc).isoformat())
            st.session_state.setdefault('model_trials', []).append(record)
            st.success('Added to this browser session. Download the comparison to keep it.')
    records = [x for x in st.session_state.get('model_trials', []) if x['brief_id'] == identity]
    if records:
        st.subheader('Same-brief comparison')
        st.caption('Changing the topic or source material starts a different comparison. Ratings are your assessments, not benchmark results. Session records can be lost on refresh or restart.')
        for record in records:
            with st.expander(f"{record['tool']} · {record['model']} · {record['rating']}/5"):
                st.text(record['answer'])
                st.text(record['reviewer_notes'])
        st.download_button('Download comparison', json.dumps(records, indent=2), 'ad-doctor-comparison.json', 'application/json')
