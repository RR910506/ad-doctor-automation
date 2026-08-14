from ui.components import empty_state, page_header, status_badge


def _bullets(st, title, values):
    st.markdown(f'#### {title}')
    if values:
        for value in values:
            st.write(f'• {value}')
    else:
        st.caption('None recorded.')


def render(st, service, actions):
    page_header(st, 'Content Studio', 'Review research, diagnosis, carousel, quality and history in one editorial workspace.')
    vm = service.get('content_studio')
    if not vm:
        empty_state(st, 'No Content Package selected', 'Structured content will appear after a qualified Case completes research and generation.', 'Open Cases or enable demo mode.')
        return

    left, main = st.columns([.8, 2.2])
    labels = [item.title for item in vm.queue]
    selected = left.radio('Case queue', labels, help='Choose a case package to review.')
    data = vm.packages.get(selected, vm.selected)
    left.caption(f'{len(labels)} cases in the editorial queue')

    main.subheader(data['title'])
    main.caption(data['hook'])
    status_badge(main, data['status'])
    score_col, source_col = main.columns(2)
    score_col.metric('Quality score', f"{data['quality']}/100")
    source_col.metric('Distinct publications', data['sources'])

    tabs = main.tabs(['Overview', 'Research', 'Diagnosis', 'Carousel', 'Article', 'Quality', 'History'])
    with tabs[0]:
        main.write(f"**Evidence coverage:** {data['sources']} distinct approved publications")
        main.info('Publishing remains blocked until quality gates pass and a human explicitly approves the package.')
    with tabs[1]:
        for label, key in [('Verified facts', 'facts'), ('Source claims', 'claims'), ('Contradictions', 'contradictions'), ('Unknowns', 'unknowns')]:
            _bullets(main, label, data[key])
        main.markdown('#### Source catalog')
        for source in data['source_catalog']:
            with main.container(border=True):
                main.write(f"**{source['publication']}** · {source['article']}")
                main.caption(f"Evidence use: {source['used_for']}")
    with tabs[2]:
        for label, value in data['diagnosis'].items():
            main.markdown(f'#### {label}')
            main.write(value)
    with tabs[3]:
        for slide in data['slides']:
            with main.container(border=True):
                main.caption(f"SLIDE {slide['number']} · {slide['type'].upper()}")
                main.markdown(f"### {slide['headline']}")
                main.write(slide['body'])
                main.write(f"**Visual direction:** {slide['visual_direction']}")
                main.caption(f"Source support: {slide['source_support']} · Sources: {', '.join(slide['sources']) or 'Opinion / analysis'}")
                for warning in slide['quality_warnings']:
                    main.warning(warning)
    with tabs[4]:
        main.markdown('#### Long-form article preview')
        main.write(data['article'])
        main.caption('Display preview only. The validated Evidence Pack remains the source boundary.')
    with tabs[5]:
        quality = data['quality_detail']
        gate_col, score_col = main.columns(2)
        gate_col.metric('Gate status', quality['gate'])
        score_col.metric('Overall score', f"{data['quality']}/100")
        main.markdown('#### Dimension scores')
        dimension_cols = main.columns(len(quality['dimensions']))
        for col, (label, score) in zip(dimension_cols, quality['dimensions'].items()):
            col.metric(label, f'{score}/100')
        _bullets(main, 'Blockers', quality['blockers'])
        _bullets(main, 'Warnings', quality['warnings'])
        main.markdown('#### Traceability')
        main.info(quality['traceability'])
    with tabs[6]:
        _bullets(main, 'Package versions', data['versions'])
        _bullets(main, 'Quality-review versions', data['quality_versions'])
        main.markdown('#### Workflow history')
        for event in data['timeline']:
            main.write(f"**{event['time']} · {event['label']}**")
            main.caption(event['detail'])
        _bullets(main, 'Approval history', data['approval_history'])

    main.divider()
    cols = main.columns(4)
    for col, label in zip(cols, ['Request revision', 'Approve content', 'Regenerate section', 'Publish']):
        col.button(label, disabled=True, help='Prepared for a later safely connected phase.', use_container_width=True)
