def get_page_state(st,key,default):return st.session_state.get(key,default)
def set_page_state(st,key,value):st.session_state[key]=value
