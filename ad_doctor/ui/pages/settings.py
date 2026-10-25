from ui.components import empty_state, page_header, status_badge


def _provider(st, item, input_label=None):
    st.write(f'**{item.name}**')
    status_badge(st, item.status)
    st.write(item.message)
    st.caption(f'Configuration source: {item.source}')
    if input_label:
        st.text_input(input_label, value='', type='password', placeholder='Enter a replacement value', help='Existing secret values are never loaded into this field.')
        st.button(f'Save {item.name} replacement', disabled=True, help='Settings are read-only in Phase 4A.')


def render(st, service, actions):
    page_header(st, 'Settings', 'Manage product configuration and safe provider connectivity.')
    items = service.get('settings')
    if not items:
        empty_state(st, 'Settings adapter not connected', 'No secret values are displayed by the modern shell.', 'Use Legacy Workspace for existing configuration during migration.')
        return
    by_name = {item.name: item for item in items}
    tabs = st.tabs(['General', 'Content Framework', 'Research Provider', 'Content Provider', 'Google Workspace', 'Email Delivery', 'Automation', 'Security', 'Advanced'])
    with tabs[0]:
        st.write('Ad Doctor · Marketing intelligence and opinion platform')
        st.caption('Human approval is mandatory before publishing or delivery.')
        if not service.demo:
            st.markdown('#### Workspace capabilities')
            labels = {
                'legacy_pipeline_available': 'Legacy pipeline',
                'case_files_available': 'Case architecture',
                'structured_content_available': 'Structured content',
                'publishing_available': 'Publishing',
                'subscribers_available': 'Subscriber delivery',
            }
            for key, label in labels.items():
                available = service.capabilities.get(key, False)
                state = 'Shadow enabled' if key == 'case_files_available' and available and service.capabilities.get('case_mode') == 'shadow' else 'Active' if key == 'legacy_pipeline_available' and available else 'Available' if available else 'Not activated'
                st.write(f'**{label}:** {state}')
    with tabs[1]:
        st.info('The diagnosis-led editorial framework is active. Research and content generation remain separate stages.')
    with tabs[2]:
        _provider(st, by_name['Browser Research'])
        st.write('**Retrieval:** RSS/HTTP first, Playwright Chromium fallback')
        st.write('**Google Search grounding:** Disabled')
        st.caption('The SQLite Sources registry is authoritative. Google Sheets is optional.')
    with tabs[3]:
        _provider(st, by_name['Gemini Content'])
        st.write('**Primary model:** gemini-3.7-flash · **503-only fallback:** gemini-3.5-flash')
        st.write('**Grounding:** Disabled')
        _provider(st, by_name['Gemini Quality'])
        st.write('**Grounding:** Disabled')
    with tabs[4]:
        _provider(st, by_name['Google Workspace'])
        st.file_uploader('Replacement Google service-account file', type=['json'], disabled=True, help='Existing service-account JSON is never displayed.')
    with tabs[5]:
        _provider(st, by_name['Email'], 'Replacement SMTP password')
    with tabs[6]:
        st.info('Automation settings are view-only here. No scheduler or worker is started by opening this page.')
    with tabs[7]:
        st.info('Secrets are replacement-only inputs. Existing values are never rendered or prefilled.')
    with tabs[8]:
        st.warning('Advanced settings are visually separated and intended for administrators.')
        if 'Perplexity' in by_name:
            _provider(st, by_name['Perplexity'])
        if 'OpenAI (optional)' in by_name:
            _provider(st, by_name['OpenAI (optional)'])
        if not service.demo:
            st.link_button('Open Legacy Workspace', '?page=legacy')
