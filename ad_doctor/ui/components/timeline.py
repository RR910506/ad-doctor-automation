def timeline(st,events):
 for event in events:st.markdown(f"**{event.get('label','Update')}**  \n{event.get('time','')} · {event.get('detail','')}")
