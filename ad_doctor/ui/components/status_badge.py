from html import escape
from ui.status_vocabulary import status_display
def status_badge(st,status):
 d=status_display(status);st.markdown(f'<span class="ad-badge {d.category}" title="{escape(d.description)}">{escape(d.label)}</span>',unsafe_allow_html=True)
 return d
