from ui.components import empty_state, page_header, status_badge


def _bullets(st, title, values):
    st.markdown(f"#### {title}")
    if values:
        for value in values:
            st.write(f"• {value}")
    else:
        st.caption("None recorded.")


def render(st, service, actions):
    page_header(st, "My content", "Read your draft, review its sources, and decide what happens next.")
    vm = service.get("content_studio")
    if not vm:
        empty_state(st, "Your drafts will appear here", "There is no prepared content to review yet.", "Your expert can check sources and research progress in Expert workspace.")
        return
    left, main = st.columns([0.8, 2.2])
    labels = [item.title for item in vm.queue]
    selected = left.radio("Case queue", labels, help="Choose an editorial item to review.")
    data = vm.packages.get(selected, vm.selected)
    left.caption(f"{len(labels)} items in the editorial queue")
    main.subheader(data["title"])
    main.caption(data["hook"])
    status_badge(main, data["status"])
    score_col, source_col = main.columns(2)
    score_col.metric("Quality score", f"{data['quality']}/100" if data["quality"] is not None else "Not generated")
    source_col.metric("Distinct publications", data["sources"])
    article_tab, carousel_tab, expert_tab = main.tabs(["Article", "Carousel", "Expert details"])
    tabs = [expert_tab, expert_tab, expert_tab, carousel_tab, article_tab, expert_tab, expert_tab]
    with tabs[0]:
        st.write(f"**Evidence coverage:** {data['sources']} distinct approved publications")
        st.info("Publishing remains blocked until quality gates pass and a human explicitly approves the package.")
    with tabs[1]:
        for label, key in (("Verified facts", "facts"), ("Source claims", "claims"), ("Contradictions", "contradictions"), ("Unknowns", "unknowns")):
            _bullets(st, label, data[key])
        st.markdown("#### Source catalog")
        if not data["source_catalog"]:
            st.caption("No structured source catalog is stored for this legacy draft.")
        for source in data["source_catalog"]:
            with st.container(border=True):
                st.write(f"**{source.get('publication', 'Approved publication')}** · {source.get('article', 'Stored source')}")
                st.caption(f"Evidence use: {source.get('used_for', 'Research support')}")
    with tabs[2]:
        for label, value in data["diagnosis"].items():
            st.markdown(f"#### {label}")
            st.write(value)
    with tabs[3]:
        if not data["slides"]:
            st.caption("No carousel output is stored for this editorial item.")
        for slide in data["slides"]:
            with st.container(border=True):
                st.caption(f"SLIDE {slide['number']} · {str(slide['type']).upper()}")
                st.markdown(f"### {slide['headline']}")
                st.write(slide["body"])
                st.write(f"**Visual direction:** {slide['visual_direction']}")
                st.caption(f"Source support: {slide['source_support']} · Sources: {', '.join(slide['sources']) or 'Opinion / analysis'}")
                for warning in slide["quality_warnings"]:
                    st.warning(warning)
    with tabs[4]:
        st.markdown("#### Long-form article preview")
        st.write(data["article"])
        st.caption("Display preview only. Stored research remains the source boundary.")
    with tabs[5]:
        quality = data["quality_detail"]
        gate_col, score_col = st.columns(2)
        gate_col.metric("Gate status", quality["gate"])
        score_col.metric("Overall score", f"{data['quality']}/100" if data["quality"] is not None else "Not generated")
        st.markdown("#### Dimension scores")
        if quality["dimensions"]:
            dimension_cols = st.columns(len(quality["dimensions"]))
            for col, (label, score) in zip(dimension_cols, quality["dimensions"].items()):
                col.metric(label, f"{score}/100")
        else:
            st.caption("No automated dimension scores are stored for this draft.")
        _bullets(st, "Blockers", quality["blockers"])
        _bullets(st, "Warnings", quality["warnings"])
        st.markdown("#### Traceability")
        st.info(quality["traceability"])
    with tabs[6]:
        _bullets(st, "Package versions", data["versions"])
        _bullets(st, "Quality-review versions", data["quality_versions"])
        st.markdown("#### Workflow history")
        for event in data["timeline"]:
            st.write(f"**{event['time']} · {event['label']}**")
            st.caption(event["detail"])
        _bullets(st, "Approval history", data["approval_history"])
    main.divider()
    output_id = data.get("approval_content_output_id")
    reviewer = main.text_input("Reviewer identity", placeholder="Name or work email", key=f"reviewer_{output_id}")
    notes = main.text_area("Review notes", placeholder="Reason or requested corrections", key=f"review_notes_{output_id}")
    confirmed = main.checkbox("Confirm this editorial decision", key=f"confirm_review_{output_id}")
    cols = main.columns(4)
    action_specs = (("Request revision", actions.request_revision), ("Approve content", actions.approve_content), ("Reject", actions.reject_content))
    for index, (label, action) in enumerate(action_specs):
        if cols[index].button(label, type="primary" if label == "Approve content" else "secondary", disabled=not output_id or not reviewer or not confirmed, use_container_width=True):
            result = action(output_id, reviewer_id=reviewer, notes=notes, confirmed=confirmed)
            (main.success if result.success else main.error)(result.user_message)
            if result.success and result.refresh_required:
                st.rerun()
    cols[3].button("Publish", disabled=True, help="Publishing remains disabled in Phase 4B.", use_container_width=True)
    main.caption(actions.regenerate("section").user_message)
