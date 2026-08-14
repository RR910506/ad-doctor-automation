from __future__ import annotations
import ast,importlib,os,sys,unittest
from pathlib import Path
from unittest import mock
PROJECT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(PROJECT))
from ui.navigation import GROUPS,ALL,resolve_page
from ui.status_vocabulary import all_statuses,status_display
from ui.theme import CSS,TOKENS
from ui.services.ui_query_service import UIQueryService,demo_mode_enabled
from ui.components.pagination import paginate
from src.domain.workflow import WorkflowStatus
PAGES=('dashboard','sources','articles','qualification','cases','content_studio','publishing','subscribers','automation','logs','settings')
class ArchitectureTests(unittest.TestCase):
 def test_app_is_thin_and_legacy_route_available(self):
  text=(PROJECT/'app.py').read_text();self.assertLess(len(text.splitlines()),30);self.assertIn('ui.legacy_compat',text);self.assertIn('render_dashboard_shell',text)
 def test_all_page_modules_import_and_contain_no_sql_or_provider_calls(self):
  for page in PAGES:
   module=importlib.import_module(f'ui.pages.{page}');self.assertTrue(callable(module.render));text=(PROJECT/'ui'/'pages'/f'{page}.py').read_text().lower();self.assertNotIn('select ',text);self.assertNotIn('get_connection',text);self.assertNotIn('import openai',text);self.assertNotIn('from openai',text);self.assertNotIn('googleapiclient',text)
 def test_view_models_have_no_secret_fields(self):
  text=(PROJECT/'ui'/'view_models'/'models.py').read_text().lower();self.assertNotIn('password',text);self.assertNotIn('api_key',text);self.assertNotIn('token',text)
class DesignNavigationTests(unittest.TestCase):
 def test_theme_and_accessibility_tokens(self):self.assertIn(':focus-visible',CSS);self.assertIn('@media(max-width:760px)',CSS);self.assertIn('primary',TOKENS)
 def test_status_vocabulary_covers_workflow(self):
  labels=all_statuses()
  for status in WorkflowStatus:self.assertIn(status.value,labels);self.assertTrue(status_display(status.value).label)
 def test_navigation_has_eleven_pages_and_groups(self):self.assertEqual(set(ALL),set(PAGES));self.assertEqual(tuple(GROUPS),('WORKSPACE','DISTRIBUTION','SYSTEM'))
 def test_route_resolution(self):self.assertEqual(resolve_page('cases'),'cases');self.assertEqual(resolve_page('unknown'),'dashboard');self.assertEqual(resolve_page('legacy'),'legacy')
class FixtureFilteringTests(unittest.TestCase):
 def test_demo_defaults_false_and_is_in_memory(self):self.assertFalse(demo_mode_enabled({}));service=UIQueryService(demo=True);self.assertTrue(service.demo);self.assertIsNotNone(service.get('dashboard'))
 def test_non_demo_has_guided_empty_models(self):self.assertEqual(UIQueryService(demo=False).get('sources'),())
 def test_fixture_states_are_safe_and_explicit(self):
  self.assertEqual(UIQueryService(demo=True,state='empty').get('sources'),())
  self.assertEqual(UIQueryService(demo=True,state='loading').state,'loading')
  self.assertEqual(UIQueryService(demo=True,state='warning').state,'warning')
  self.assertFalse(UIQueryService(demo=True,state='warning').get('automation').enabled)
  self.assertEqual(UIQueryService(demo=False,state='warning').state,'populated')
  shell=(PROJECT/'ui'/'app_shell.py').read_text(encoding='utf-8');self.assertIn('AD_DOCTOR_APP_SCHEDULER_ENABLED',shell);self.assertIn('demo_mode_enabled()',shell)
 def test_dashboard_populated_and_health_safe(self):
  vm=UIQueryService(demo=True).get('dashboard');self.assertTrue(vm.metrics);self.assertEqual(vm.attention[0].status,'quality_review_failed');self.assertNotIn('API_KEY',str(vm.health))
 def test_source_search_filter_sort_pagination_and_empty(self):
  service=UIQueryService(demo=True);items,meta=service.list('sources',search='campaign',page_size=1);self.assertEqual(len(items),1);self.assertEqual(meta['total'],1);items,_=service.list('sources',status='inactive');self.assertEqual(items[0].status,'inactive');items,_=service.list('sources',sort='A–Z');self.assertEqual(items[0].title,'Ad Age');self.assertEqual(service.list('sources',search='missing')[0],[])
 def test_article_filters_and_raw_html_omitted(self):
  items,_=UIQueryService(demo=True).list('articles',search='Nike',filters={'publication':'Ad Age'});self.assertEqual(len(items),1);self.assertNotIn('html',str(items[0].details).lower())
 def test_qualification_states_and_distinct_count(self):
  items=UIQueryService(demo=True).get('qualification');self.assertEqual(items[0].details['count'],4);self.assertTrue(items[0].details['supporting']);self.assertTrue(any(x.status=='qualification_pending' for x in items));self.assertTrue(any(x.status=='rejected' for x in items))
 def test_cases_multi_filter_and_friendly_status(self):
  items,_=UIQueryService(demo=True).list('cases',filters={'brand':'Nike'});self.assertEqual(len(items),1);self.assertEqual(status_display(items[0].status).label,'Ready for review');self.assertNotIn('payload_json',str(items))
 def test_content_studio_sections(self):
  vm=UIQueryService(demo=True).get('content_studio');self.assertTrue(vm.queue);self.assertIn("Doctor’s Opinion",vm.selected['diagnosis']);self.assertEqual(len(vm.selected['slides']),10);self.assertTrue(vm.selected['source_catalog']);self.assertTrue(vm.selected['quality_detail']['dimensions']);self.assertTrue(vm.selected['quality_versions']);self.assertTrue(vm.selected['approval_history']);failure=vm.packages[vm.queue[1].title];self.assertEqual(failure['quality_detail']['gate'],'Fail');self.assertTrue(failure['quality_detail']['blockers'])
 def test_publishing_subscribers_automation_logs_settings(self):
  from dataclasses import asdict
  s=UIQueryService(demo=True);self.assertTrue(s.get('publishing').ready);self.assertTrue(all('***@' in x.title for x in s.get('subscribers')));self.assertTrue(s.get('automation').history);logs,_=s.list('logs');self.assertTrue(logs);self.assertTrue(all('password' not in x.title.lower() for x in logs));self.assertTrue(all('secret' not in asdict(x) for x in s.get('settings')))
 def test_pagination_bounds(self):
  page,meta=paginate(list(range(23)),3,10);self.assertEqual(page,[20,21,22]);self.assertEqual(meta['pages'],3)
 def test_external_call_sentinel(self):
  import socket
  with mock.patch.object(socket,'create_connection',side_effect=AssertionError('network')),mock.patch('requests.sessions.Session.request',side_effect=AssertionError('http')):self.assertTrue(UIQueryService(demo=True).get('sources'))
if __name__=='__main__':unittest.main()
