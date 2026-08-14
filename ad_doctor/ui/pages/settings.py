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
    with tabs[1]:
        st.info('The diagnosis-led editorial framework is active. Research and content generation remain separate stages.')
    with tabs[2]:
        _provider(st, by_name['Perplexity'], 'Replacement Perplexity API key')
    with tabs[3]:
        _provider(st, by_name['OpenAI'], 'Replacement OpenAI API key')
    with tabs[4]:
        _provider(st, by_name['Google Workspace'])
        st.file_uploader('Replacement Google service-account file', type=['json'], disabled=True, help='Existing service-account JSON is never displayed.')
    with tabs[5]:
        _provider(st, by_name['Email'], 'Replacement SMTP password')
    with tabs[6]:
        st.info('Automation settings are view-only in Phase 4A. No scheduler or worker is started by this page.')
    with tabs[7]:
        st.info('Secrets are replacement-only inputs. Existing values are never rendered or prefilled.')
    with tabs[8]:
        st.warning('Advanced settings are visually separated and intended for administrators.')
        st.link_button('Open Legacy Workspace', '?page=legacy')
