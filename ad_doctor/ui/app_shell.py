from __future__ import annotations
import os
import streamlit as st
from ui.navigation import GROUPS,ALL,resolve_page
from ui.theme import apply_theme
from ui.services.ui_query_service import UIQueryService,demo_mode_enabled
from ui.services.ui_action_service import UIActionService
from ui.services.demo_action_service import DemoActionService
from ui.pages import dashboard,sources,articles,qualification,cases,content_studio,publishing,subscribers,automation,logs,settings
PAGES={'dashboard':dashboard,'sources':sources,'articles':articles,'qualification':qualification,'cases':cases,'content_studio':content_studio,'publishing':publishing,'subscribers':subscribers,'automation':automation,'logs':logs,'settings':settings}
def render_app(*, community_demo=False):
 requested=resolve_page(str(st.query_params.get('page','dashboard')))
 if community_demo and requested=='legacy':requested='dashboard'
 if demo_mode_enabled() and requested=='legacy':requested='dashboard'
 if requested=='legacy':
  from ui.legacy_app import render_dashboard_shell
  prior_scheduler=os.environ.get('AD_DOCTOR_APP_SCHEDULER_ENABLED')
  if demo_mode_enabled():os.environ['AD_DOCTOR_APP_SCHEDULER_ENABLED']='0'
  try:render_dashboard_shell()
  finally:
   if demo_mode_enabled():
    if prior_scheduler is None:os.environ.pop('AD_DOCTOR_APP_SCHEDULER_ENABLED',None)
    else:os.environ['AD_DOCTOR_APP_SCHEDULER_ENABLED']=prior_scheduler
  st.markdown('<div style="position:fixed;right:1rem;top:.75rem;z-index:999999;background:#16202A;color:white;border-radius:999px;padding:.5rem .8rem;font:600 13px sans-serif;box-shadow:0 4px 18px #0003">Legacy compatibility workspace · <a href="?page=dashboard" target="_self" style="color:#fff;text-decoration:underline">Return to modern app</a></div>',unsafe_allow_html=True)
  return
 st.set_page_config(page_title='Ad Doctor',page_icon='🩺',layout='wide',initial_sidebar_state='auto');apply_theme(st)
 fixture_state=str(st.query_params.get('state','populated'))
 service=UIQueryService(demo=True,state=fixture_state) if community_demo else UIQueryService(state=fixture_state);actions=DemoActionService() if service.demo else UIActionService()
 with st.sidebar:
  st.markdown('<div class="ad-kicker">THE AD DOCTOR</div><h2>Content Intelligence</h2>',unsafe_allow_html=True)
  if service.demo_label:
   if service.demo:st.info(service.demo_label,icon='🧪')
   else:st.success(service.demo_label)
  choices=[]
  for group,items in GROUPS.items():
   st.caption(group);choices.extend(items)
   for item in items:
    if st.button(f'{item.icon}  {item.label}',key=f'nav_{item.key}',use_container_width=True,type='primary' if item.key==requested else 'secondary',help=item.description):st.query_params['page']=item.key;requested=item.key;st.rerun()
  st.divider();st.caption('Marketing examined. Strategy diagnosed.')
 if service.demo and service.state=='warning':st.warning('Fixture warning: a recoverable upstream issue is being demonstrated.',icon='⚠️')
 if service.demo and service.state=='loading':
  st.markdown('<div class="ad-skeleton" style="height:7rem"></div><br><div class="ad-skeleton" style="height:18rem"></div>',unsafe_allow_html=True);return
 PAGES[requested].render(st,service,actions)
