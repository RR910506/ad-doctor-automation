"""Private Ad Doctor interface. Provider and workflow logic lives in services."""
import hmac
import json
import os
import time
from datetime import datetime, time as daily_time
from zoneinfo import ZoneInfo

import streamlit as st

from src.services.ai_providers import ProviderRegistry, ProviderError
from src.services.automation_service import AutomationService
from src.services.automation_store import build_store, StoreError
from src.services.team_publishing import PublishingError
from ui.components import page_header, status_badge
from ui.theme import apply_theme


LABELS = {'pending_review': 'Ready for your review', 'needs_revision': 'Held for revision',
          'processing': 'Preparing content', 'blocked': 'Held · evidence or provider issue',
          'completed_with_gaps': 'Finished with collection gaps', 'completed': 'Finished',
          'interrupted': 'Interrupted · progress saved', 'configuration_required': 'AI configuration required'}


def configured(name):
    if os.environ.get(name):
        return os.environ[name]
    try:
        return str(st.secrets.get(name, ''))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return ''


def _local_time(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo('Asia/Kolkata')).strftime('%d %b %Y, %I:%M %p IST') if value else ''


def _navigate(page):
    st.session_state['team_page'] = page
    st.rerun()


def main():
    st.set_page_config(page_title='Ad Doctor · Content Intelligence', page_icon='🩺', layout='wide')
    apply_theme(st)
    password = configured('AD_DOCTOR_ACCESS_PASSWORD')
    if len(password) < 16:
        st.info('The owner must configure AD_DOCTOR_ACCESS_PASSWORD (at least 16 characters) in Streamlit Secrets.')
        return
    if not hmac.compare_digest(str(st.session_state.get('_access_proof', '')).encode(), password.encode()):
        page_header(st, 'Ad Doctor team access', 'Sign in to your private content-intelligence workspace.')
        with st.form('team_access'):
            submitted = st.text_input('Workspace password', type='password')
            if st.form_submit_button('Open workspace'):
                if time.monotonic() < st.session_state.get('_access_retry', 0):
                    st.error('Wait a few seconds before trying again.')
                elif hmac.compare_digest(submitted.encode(), password.encode()):
                    st.session_state['_access_proof'] = password
                    st.rerun()
                else:
                    st.session_state['_access_retry'] = time.monotonic() + 5
                    st.error('Incorrect workspace password.')
        return
    registry = ProviderRegistry(configured)
    try:
        store = build_store({name: configured(name) for name in ('SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY', 'AD_DOCTOR_STATE_DATABASE')})
        service = AutomationService(store, registry)
        requested = st.session_state.get('team_page', 'Today')
        with st.sidebar:
            st.markdown('<div class="ad-kicker">THE AD DOCTOR</div><h2>Content Intelligence</h2>', unsafe_allow_html=True)
            for label in ('Today', 'My content', 'Publishing'):
                if st.button(label, key='team_nav_' + label, type='primary' if requested == label else 'secondary', use_container_width=True):
                    _navigate(label)
            with st.expander('Expert workspace', expanded=requested in ('AI settings', 'Model Lab', 'Automation', 'Source coverage')):
                for label in ('AI settings', 'Model Lab', 'Automation', 'Source coverage'):
                    if st.button(label, key='team_nav_' + label, use_container_width=True):
                        _navigate(label)
            st.divider()
            st.caption('Marketing examined. Strategy diagnosed.')
            if st.button('Sign out'):
                st.session_state.clear()
                st.rerun()
        if not store.shared:
            st.caption('Trial storage · Download important results. A Streamlit hosting restart can erase local records. Shared storage enables daily cloud operation.')
        pages = {'Today': _today, 'My content': _content, 'Publishing': _publishing,
                 'AI settings': _ai_settings, 'Model Lab': _model_lab, 'Automation': _automation, 'Source coverage': _coverage}
        pages.get(requested, _today)(service, registry)
    except (StoreError, ProviderError, PublishingError, ValueError) as exc:
        st.error(str(exc))


def _today(service, registry):
    page_header(st, 'Today', 'See what we collected and choose what to review.')
    runs, cases = service.list_runs(10), service.store.list('case', 100)
    latest = runs[0] if runs else {}
    report = latest.get('collection', {})
    counts = report.get('collection', {})
    first, second, third = st.columns(3)
    first.metric('Articles saved · latest run', report.get('articles_saved', counts.get('inserted', 0) + counts.get('updated', 0)))
    second.metric('Topics ready to research · latest run', latest.get('eligible_topics', 0))
    third.metric('Waiting for your review · all runs', sum(case['status'] == 'pending_review' for case in cases))
    if latest:
        st.caption(f"{_local_time(latest.get('started_at'))} · {LABELS.get(latest['status'], latest['status'])}")
        st.write(latest.get('message', latest.get('stage', '')))
    action, review, refresh = st.columns([2, 1, 1])
    if action.button('Collect stories & prepare content', type='primary', disabled=latest.get('status') == 'running'):
        with st.spinner('Collecting publications, checking evidence and preparing content. This may take several minutes…'):
            result = service.run_now()
        st.session_state['last_run_status'] = result['status']
        st.rerun()
    if review.button('Review my content'):
        _navigate('My content')
    if refresh.button('Refresh'):
        st.rerun()
    st.subheader('What we collected · latest run')
    articles = report.get('articles', [])
    if not articles:
        st.info('No saved stories in this workspace yet. Collection uses your approved publication list automatically.')
    for article in articles[:40]:
        with st.container(border=True):
            st.write('**' + article.get('headline', 'Collected article') + '**')
            st.caption(article.get('source_name', 'Approved publication'))
            if article.get('article_url'):
                st.link_button('Read original', article['article_url'])
    st.caption('Approved publications → collected stories → research → content for your approval.')
    st.caption('Collection is performed by code. Research, writing and review use the AI models selected in Expert workspace.')


def _content(service, registry):
    page_header(st, 'My content', 'Read your draft, review its evidence, and decide what happens next.')
    cases = service.store.list('case', 100)
    if not cases:
        st.info('Prepared content appears here after a topic qualifies and its evidence is checked.')
        return
    left, main_area = st.columns([0.8, 2.2])
    index = left.radio('Case queue', range(len(cases)), format_func=lambda index: cases[index]['topic'])
    case = cases[index]
    draft = case.get('draft') or {}
    if case['status'] in ('needs_revision', 'blocked', 'processing'):
        main_area.warning('This item is held. Its output has not passed all checks and cannot be approved.')
    main_area.subheader(draft.get('title', case['topic']))
    main_area.caption(LABELS.get(case['status'], case['status']))
    evidence = case.get('evidence') or {}
    sources = evidence.get('source_catalog', [])
    quality = case.get('quality') or {}
    first, second = main_area.columns(2)
    first.metric('Quality gate', 'Passed' if quality.get('passed') else 'Held / not completed')
    second.metric('Distinct publisher groups', len({s.get('publisher_network') for s in sources}))
    article_tab, carousel_tab, expert_tab = main_area.tabs(['Article', 'Carousel', 'Expert details'])
    with article_tab:
        for field, label in [('diagnosis', 'Diagnosis'), ('doctors_opinion', 'Doctor’s Opinion'), ('prescription', 'Marketing prescription')]:
            if draft.get(field):
                st.markdown('#### ' + label)
                st.write(draft[field].get('text', '') if isinstance(draft[field], dict) else '')
        st.markdown('#### Article')
        article = draft.get('article', [])
        for block in article if isinstance(article, list) else []:
            if isinstance(block, dict):
                st.write(block.get('text', ''))
        if not draft:
            st.info('This item was held before a draft was ready. Its reason is saved in Expert details.')
    with carousel_tab:
        carousel = draft.get('carousel', [])
        for slide in carousel if isinstance(carousel, list) else []:
            if not isinstance(slide, dict):
                continue
            with st.container(border=True):
                st.caption(f"SLIDE {slide.get('slide')}")
                st.markdown('### ' + slide.get('title', ''))
                st.write(slide.get('text', ''))
                ids = slide.get('evidence_ids', [])
                st.caption('Evidence: ' + ', '.join(str(ref) for ref in ids) if isinstance(ids, list) else 'Evidence references need correction')
        st.write(draft['caption'].get('text', '') if isinstance(draft.get('caption'), dict) else '')
    with expert_tab:
        st.json({'evidence': evidence, 'research': case.get('research'), 'quality': quality,
                 'routes': case.get('routes'), 'timeline': case.get('stages'), 'errors': case.get('errors'), 'approval': case.get('review_history', [])})
        for source in sources:
            st.link_button(source.get('publication', 'Source'), source['url'])
        st.download_button('Download evidence & audit', json.dumps(case, indent=2), f"case-{case['case_id']}.json", 'application/json')
    if draft:
        main_area.download_button('Download article & carousel', service.markdown(case), f"ad-doctor-{case['case_id']}.md", 'text/markdown')
    if quality.get('passed') and case['status'] in ('pending_review', 'approved', 'rejected'):
        with main_area.form('review_' + case['case_id']):
            reviewer = st.text_input('Reviewer identity', placeholder='Your name')
            confirmed = st.checkbox('I reviewed the article, carousel and source evidence')
            approve, reject = st.columns(2)
            approved = approve.form_submit_button('Approve content')
            rejected = reject.form_submit_button('Reject content')
            if approved or rejected:
                if not confirmed:
                    st.error('Confirm the review before recording a decision.')
                else:
                    service.review_case(case['case_id'], approved, reviewer)
                    st.rerun()


def _ai_settings(service, registry):
    page_header(st, 'AI settings', 'Choose a separate provider and model for each job.')
    st.info('Gemini is the temporary testing choice. You can change any stage below without rebuilding Ad Doctor.')
    settings = service.settings()
    catalog = {item['id']: item for item in registry.catalog()}
    routes = {}
    for stage, label in [('research', 'Research'), ('writing', 'Writing & strategy'), ('quality', 'Quality review')]:
        with st.container(border=True):
            st.subheader(label)
            options = [key for key, spec in catalog.items() if stage in spec['stages']]
            current = settings['routes'][stage]
            provider = st.selectbox('Provider', options, index=options.index(current['provider']) if current['provider'] in options else 0,
                                    format_func=lambda key: catalog[key]['label'], key='provider_' + stage)
            st.caption('API key configured' if registry.configured(provider) else f"Setup required: add {catalog[provider]['secret_name']} privately in Streamlit Secrets.")
            if st.button('Load available models', key='models_' + stage, disabled=not registry.configured(provider)):
                try:
                    st.session_state['models_' + provider] = registry.available_models(provider)
                    st.success('Model list loaded. A generation call is needed to verify inference access.')
                except ProviderError as exc:
                    st.info(str(exc))
            models = st.session_state.get('models_' + provider, [])
            default = current['model'] if provider == current['provider'] else ''
            if models:
                model = st.selectbox('Model', models, index=models.index(default) if default in models else 0, key='listed_model_' + stage + '_' + provider)
                st.caption('Listed by the provider; generation access and quota still depend on your account.')
            else:
                model = st.text_input('Model ID', value=default, key='model_' + stage + '_' + provider, placeholder='Exact model ID from the provider')
            routes[stage] = {'provider': provider, 'model': model.strip()}
    if st.button('Save AI choices', type='primary'):
        service.save_settings({'routes': routes})
        st.success('Research, writing and quality choices saved independently.')
    st.caption('Each provider requires its own API access. ChatGPT Plus and consumer Perplexity subscriptions do not supply API credit automatically. No provider is switched silently.')


def _automation(service, registry):
    page_header(st, 'Automation', 'Set the daily India time and how much content to prepare.')
    settings = service.settings()
    st.write('Shared storage: ' + ('Connected' if service.store.shared else 'Setup required'))
    if not service.store.shared:
        st.info('Daily unattended cloud operation needs shared storage and an independent worker. Setup instructions are in AUTOMATION_SETUP.md.')
    with st.form('schedule'):
        enabled = st.checkbox('Run automatically every day', value=settings['enabled'], disabled=not service.store.shared)
        hour, minute = map(int, settings['schedule_time'].split(':'))
        selected = st.time_input('Daily time · India', value=daily_time(hour, minute), step=60)
        maximum = st.number_input('Maximum topics per run', min_value=1, max_value=10, value=settings['max_topics'])
        if st.form_submit_button('Save schedule', type='primary'):
            service.save_settings({'enabled': enabled, 'schedule_time': selected.strftime('%H:%M'), 'max_topics': int(maximum)})
            st.success('Schedule saved. The separate worker must be configured for unattended runs.')
    st.caption('Each topic uses separate research, writing and quality calls, with at most one revision. Provider quotas apply.')


def _coverage(service, registry):
    page_header(st, 'Source coverage', 'See what was read, held, blocked or left queued.')
    runs = service.list_runs(30)
    if not runs:
        st.info('Collection reports appear after the first run.')
    for run in runs:
        with st.expander(f"{_local_time(run.get('started_at'))} · {LABELS.get(run['status'], run['status'])}"):
            st.json(run)
            st.download_button('Download run report', json.dumps(run, indent=2), f"run-{run['run_id']}.json", 'application/json', key='download_' + run['run_id'])


def _model_lab(service, registry):
    from src.services.model_arena import ModelArena
    page_header(st, 'Model Lab', 'Compare models against the same saved evidence brief.')
    cases = [case for case in service.store.list('case', 100) if case.get('evidence')]
    if not cases:
        st.info('Run collection and research first. Comparisons need real saved evidence.')
        return
    index = st.selectbox('Evidence brief', range(len(cases)), format_func=lambda i: cases[i]['topic'])
    case = cases[index]
    stage = st.selectbox('Job to test', ['research', 'writing', 'quality'])
    material = {'evidence': case['evidence']}
    if stage == 'quality':
        material['draft'] = case.get('draft')
    catalog = {item['id']: item for item in registry.catalog() if stage in item['stages']}
    provider = st.selectbox('Test provider', list(catalog), format_func=lambda key: catalog[key]['label'])
    model = st.text_input('Test model ID')
    arena = ModelArena(service.store, registry)
    if st.button('Run comparison experiment', disabled=not registry.configured(provider) or not model.strip()):
        with st.spinner('Running one explicit model experiment…'):
            arena.run(stage, material, {'provider': provider, 'model': model.strip()})
        st.success('Experiment saved. Lab results do not approve production evidence or content.')
    for experiment in service.store.list('experiment', 30):
        route = experiment.get('route', {})
        with st.expander(f"{route.get('provider')} · {route.get('model')} · {experiment.get('stage')}"):
            st.caption(f"Comparison brief: {experiment['brief_hash'][:12]} · {experiment.get('latency_seconds', '—')} seconds · {experiment['status']}")
            if experiment.get('output'):
                st.write(experiment['output'])
            if experiment.get('error'):
                st.warning(experiment['error'])
            with st.expander('Usage and input details'):
                st.json(experiment)
            if experiment['status'] == 'unverified':
                with st.form('rating_' + experiment['experiment_id']):
                    score = st.number_input('Your rating · 1 to 5', min_value=1, max_value=5, value=experiment.get('rating') or 3)
                    notes = st.text_input('Comparison notes', value=experiment.get('notes', ''), max_chars=2000)
                    if st.form_submit_button('Save rating'):
                        arena.rate(experiment['experiment_id'], int(score), notes)
                        st.success('Subjective rating saved for this exact brief.')


def _publishing(service, registry):
    from src.services.team_publishing import TeamPublishing
    from src.services.team_subscribers import TeamSubscribers
    page_header(st, 'Publishing', 'Export or deliver content after an explicit human approval.')
    publisher = TeamPublishing(service.store, configured, service.markdown)
    ready = publisher.readiness()
    st.write('Google Docs: ' + ('Configured' if ready.get('google_docs_configured') else 'Setup required'))
    st.write('Email: ' + ('Configured' if ready.get('email_configured') else 'Setup required'))
    subscriptions = TeamSubscribers(service.store)
    with st.expander('Subscribers and consent'):
        with st.form('subscriber_consent'):
            email = st.text_input('Subscriber email')
            note = st.text_input('How did this person agree to receive Ad Doctor emails?', max_chars=2000)
            consent = st.checkbox('This person explicitly agreed to receive these emails')
            if st.form_submit_button('Save subscriber'):
                subscriptions.add(email, note, confirmed=consent)
                st.success('Subscriber consent recorded.')
        for person in subscriptions.list_subscribers():
            st.write(person['email'] + ' · ' + person['status'])
            if person['status'] == 'active' and st.button('Unsubscribe', key='unsubscribe_' + person['subscriber_id']):
                subscriptions.unsubscribe(person['subscriber_id'])
                st.rerun()
    cases = [case for case in service.store.list('case', 100) if case['status'] == 'approved']
    if not cases:
        st.info('Approve content in My content before publishing or sending it.')
        return
    index = st.selectbox('Approved content', range(len(cases)), format_func=lambda i: cases[i]['topic'])
    case = cases[index]
    st.download_button('Download approved article & carousel', service.markdown(case), f"approved-{case['case_id']}.md", 'text/markdown')
    if st.button('Create private Google Doc', disabled=not ready.get('google_docs_configured')):
        result = publisher.publish_document(case['case_id'])
        st.json(result)
    with st.form('send_approved_email'):
        recipient = st.text_input('Consenting recipient email')
        confirmed = st.checkbox('I approve sending this content to this recipient')
        if st.form_submit_button('Send approved content', disabled=not ready.get('email_configured')):
            if confirmed:
                st.json(publisher.send_email(case['case_id'], recipient))
            else:
                st.error('Confirm the recipient and delivery first.')
    with st.form('send_subscribers'):
        st.caption(f"{len(subscriptions.list_subscribers(active_only=True))} active consenting subscriber(s). Maximum 100 per batch; existing successful deliveries are reused.")
        batch_approved = st.checkbox('I approve sending this content to the active subscriber list')
        if st.form_submit_button('Send to subscribers', disabled=not ready.get('email_configured')):
            st.json(subscriptions.deliver(case['case_id'], publisher, confirmed=batch_approved))
    with st.expander('Publication and delivery records'):
        st.json({'documents': service.store.list('publication', 30), 'deliveries': service.store.list('delivery', 100)})
