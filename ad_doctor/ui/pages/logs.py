from ui.pages.shared import render_directory
def render(st,service,actions):render_directory(st,service,'logs','Logs','Search redacted operational activity by stage and severity.',('success','warning','failed'),display='status_meta')
