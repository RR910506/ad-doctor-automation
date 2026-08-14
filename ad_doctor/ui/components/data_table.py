from html import escape
def card_list(st,items,title_key='title',subtitle_key='subtitle',meta_key='meta'):
 for item in items:st.markdown(f'<div class="ad-card"><div class="ad-row"><div><strong>{escape(str(item.get(title_key,"Untitled")))}</strong><div class="ad-muted">{escape(str(item.get(subtitle_key,"")))}</div></div><span class="ad-badge">{escape(str(item.get(meta_key,"")))}</span></div></div>',unsafe_allow_html=True)
