def quality_score(st,score,gate):st.metric('Quality score',f'{score:.0f}/100',delta=str(gate).replace('_',' ').title())
