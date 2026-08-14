from html import escape
def empty_state(st,title,message,next_step):st.markdown(f'<div class="ad-empty"><h3>{escape(title)}</h3><p>{escape(message)}</p><strong>{escape(next_step)}</strong></div>',unsafe_allow_html=True)
