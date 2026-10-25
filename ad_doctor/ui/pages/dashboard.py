"""Plain-language daily overview."""
from urllib.parse import urlparse
from ui.components import page_header
from ui.services.daily_brief_service import daily_brief


def _navigate(st, page):
    st.query_params['page'] = page
    st.rerun()


def render(st, service, actions):
    page_header(st, 'Today', 'See what we collected and choose what to review.')
    brief = daily_brief(service)
    vm = service.get('dashboard')
    metrics = dict(vm.metrics) if vm else {}
    if service.demo:
        metrics['Qualified topics'] = str(sum(x.status == 'qualified' for x in service.get('qualification')))
        studio = service.get('content_studio')
        metrics['Approval pending'] = str(sum(x.status == 'editorial_review_pending' for x in studio.queue)) if studio else '0'
        st.info('Sample walkthrough. These stories and counts are examples; no collection or AI research is running.')
    else:
        st.caption(f"{brief['date']} · India time · Collection means saved articles, not verified facts.")
    a, b, c = st.columns(3)
    a.metric('Sample stories' if service.demo else 'Collected today', brief['count'])
    b.metric('Topics ready to research', metrics.get('Qualified topics', metrics.get('Qualified cases', '0')))
    c.metric('Waiting for your review', metrics.get('Approval pending', '0'))
    st.caption('Topic and review counts include earlier work still in the workspace.')
    if st.button('Review my content', key='home_content_studio', type='primary'):
        _navigate(st, 'content_studio')
    st.subheader('Sample collected stories' if service.demo else 'What we collected today')
    if not brief['articles']:
        st.write('No articles have been collected today yet.')
        if str(metrics.get('Active sources', '0')) == '0':
            st.caption('First, add the publications you want Ad Doctor to follow.')
            if st.button('Add my sources', key='home_sources'):
                _navigate(st, 'sources')
        else:
            st.caption('Your expert can check collection schedules and any errors in Expert workspace.')
    for article in brief['articles']:
        with st.container(border=True):
            st.write(article['title'])
            st.caption(article['publication'])
            if article['summary']:
                st.write(article['summary'])
            url = article['url']
            if url and urlparse(url).scheme in ('http', 'https'):
                st.link_button('Read original', url)
    if brief['count'] > len(brief['articles']):
        st.caption(f"Showing the latest {len(brief['articles'])} of {brief['count']} articles collected today.")
    st.caption('Approved publications → collected stories → research → content for your approval.')
    st.caption('Nothing is published or emailed without human approval.')
