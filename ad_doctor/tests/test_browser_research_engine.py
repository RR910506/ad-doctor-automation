from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
import inspect
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.browser_research import (
    ArticleRetrievalService, BrowserPage, CrawlControls, CrawlMemoryRepository,
    DiscoveryService, DomainRateLimiter, FailureCircuitBreaker, ResearchBrowserService,
    ResearchDocument, canonicalize_url, publisher_network_for,
)
from src.browser_worker import BrowserCycleSummary, BrowserWorker, SourceRegistryService, source_is_due
from src.db import get_connection, initialize_database, upsert_source
from src.qualification_engine import count_unique_exact_sources
from src.services.gemini_model_policy import (
    AVAILABILITY_FALLBACK_MODEL, PRIMARY_GEMINI_MODEL,
    generate_with_availability_fallback, select_production_flash_model,
)
from src.adapters.gemini_research_provider import GeminiProviderError


SOURCE={"id":1,"source_name":"Campaign Global","website_url":"https://example.com","domain":"example.com","publisher_network":"Campaign","browser_policy":"fallback"}
FULL_HTML="""<html><head><title>Real Article</title><link rel='canonical' href='https://example.com/story?utm_source=x#part'><meta name='author' content='Editor'><meta property='article:published_time' content='2026-08-30'></head><body><article>{}</article></body></html>""".format("Evidence text. "*80)


class FakeHttp:
    def __init__(self, html=FULL_HTML,url="https://example.com/story",status=200):self.html=html;self.url=url;self.status=status;self.calls=[]
    def get(self,url,*,timeout_seconds):self.calls.append((url,timeout_seconds));return BrowserPage(self.url,self.html,self.status)


class FakeBrowser:
    def __init__(self,html=FULL_HTML):self.html=html;self.calls=[]
    def fetch(self,url,*,timeout_seconds):self.calls.append((url,timeout_seconds));return BrowserPage(url,self.html,200)


class BrowserResearchTests(unittest.TestCase):
    def controls(self,**values):return CrawlControls(domain_request_delay_seconds=0,minimum_article_characters=100,**values)

    def test_http_first_retrieval_avoids_browser(self):
        http=FakeHttp();browser=FakeBrowser();doc=ArticleRetrievalService(http=http,browser=browser,controls=self.controls()).retrieve("https://example.com/story",SOURCE)
        self.assertEqual(doc.extraction_method,"http");self.assertEqual(browser.calls,[]);self.assertEqual(doc.canonical_url,"https://example.com/story")

    def test_incomplete_http_uses_browser_fallback(self):
        http=FakeHttp("<html><title>Stub</title><body>short</body></html>");browser=FakeBrowser();doc=ArticleRetrievalService(http=http,browser=browser,controls=self.controls()).retrieve("https://example.com/story",SOURCE)
        self.assertEqual(doc.extraction_method,"browser");self.assertEqual(len(browser.calls),1)

    def test_canonicalization_and_content_hash(self):
        self.assertEqual(canonicalize_url("https://Example.com/a/?utm_source=x&keep=1#x"),"https://example.com/a?keep=1")
        doc=ArticleRetrievalService(http=FakeHttp(),browser=FakeBrowser(),controls=self.controls()).retrieve("https://example.com/story",SOURCE)
        self.assertEqual(doc.content_hash,sha256(doc.text.encode()).hexdigest())

    def test_discovery_is_approved_domain_only(self):
        feed="<rss><channel><item><link>https://example.com/a</link></item><item><link>https://evil.test/a</link></item></channel></rss>"
        self.assertEqual(DiscoveryService().discover_feed(feed,SOURCE),["https://example.com/a"])
        sitemap="<urlset><url><loc>https://example.com/b</loc></url><url><loc>https://evil.test/b</loc></url></urlset>"
        self.assertEqual(DiscoveryService().discover_sitemap(sitemap,SOURCE),["https://example.com/b"])

    def test_campaign_variants_are_one_publisher_network(self):
        rows=[{"source_id":1,"source_name":"Campaign Asia","publisher_network":"Campaign"},{"source_id":2,"source_name":"Campaign India","publisher_network":"Campaign"},{"source_id":3,"source_name":"Campaign Global","publisher_network":"Campaign"}]
        self.assertEqual(publisher_network_for("Campaign India"),"Campaign");self.assertEqual(count_unique_exact_sources(rows),1)

    def test_crawl_memory_skips_unchanged_content_and_preserves_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            db=Path(temporary)/"test.db";initialize_database(db)
            with closing(get_connection(db)) as c:
                upsert_source(c,SOURCE);source_id=c.execute("SELECT id FROM sources").fetchone()[0]
                doc=ArticleRetrievalService(http=FakeHttp(),browser=FakeBrowser(),controls=self.controls()).retrieve("https://example.com/story",{**SOURCE,"id":source_id})
                memory=CrawlMemoryRepository(c);self.assertFalse(memory.unchanged(doc));memory.record(source_id,doc);self.assertTrue(memory.unchanged(doc))
                c.execute("INSERT INTO topics(exact_topic,normalized_topic,status) VALUES('t','t','qualified')");topic=c.execute("SELECT id FROM topics").fetchone()[0]
                c.execute("INSERT INTO cases(case_id,topic_id,canonical_topic,workflow_status,qualification_status,created_at,updated_at) VALUES('case',?,'t','qualified','qualified',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",(topic,))
                self.assertEqual(ResearchBrowserService.store_for_case(c,"case",[doc]),1)
                self.assertEqual(c.execute("SELECT provenance FROM research_documents").fetchone()[0],"browser_research_engine")

    def test_rate_limit_and_circuit_breaker(self):
        clock=[0.0];sleeps=[]
        def sleep(value):sleeps.append(value);clock[0]+=value
        limiter=DomainRateLimiter(2,clock=lambda:clock[0],sleeper=sleep);limiter.wait("https://example.com/a");limiter.wait("https://example.com/b")
        self.assertEqual(sleeps,[2.0])
        breaker=FailureCircuitBreaker(2);breaker.record_failure("example.com");breaker.record_failure("example.com");self.assertFalse(breaker.allow("example.com"));breaker.record_success("example.com");self.assertTrue(breaker.allow("example.com"))

    def test_resource_caps_apply(self):
        retrieval=SimpleNamespace(controls=self.controls(max_pages_per_source_per_cycle=1,max_pages_per_cycle=1),retrieve=lambda url,source:url)
        result=ResearchBrowserService(retrieval).retrieve_documents(["https://example.com/a","https://example.com/b"],[SOURCE])
        self.assertEqual(result,["https://example.com/a"])

    def test_google_sheet_is_optional(self):
        self.assertFalse(SourceRegistryService(Path("unused.db")).optional_google_sheet_sync(None))

    def test_cloud_safe_module_has_no_windows_paths(self):
        import src.browser_research as module
        self.assertNotRegex(inspect.getsource(module),r"[A-Za-z]:\\")

    def test_browser_worker_once_records_scheduler_telemetry(self):
        feed="<rss><channel><item><link>https://example.com/story</link></item></channel></rss>"
        doc=ResearchDocument("https://example.com/story","https://example.com/story","Story","Campaign Global","Campaign",datetime.now(UTC).isoformat(),"Evidence text. "*30,"Editor","2026-08-30","http",sha256(("Evidence text. "*30).encode()).hexdigest(),200)
        retrieval=SimpleNamespace(retrieve=lambda url,source:doc)
        with tempfile.TemporaryDirectory() as temporary:
            db=Path(temporary)/"test.db";initialize_database(db)
            with closing(get_connection(db)) as c:upsert_source(c,{**SOURCE,"rss_url":"https://example.com/feed"});c.commit()
            summary=BrowserWorker(database_path=db,controls=self.controls(),http=FakeHttp(feed,url="https://example.com/feed"),retrieval=retrieval).run_once()
            self.assertEqual(summary.articles_fetched,1)
            with closing(get_connection(db)) as c:self.assertEqual(c.execute("SELECT status FROM browser_worker_runs").fetchone()[0],"complete")

    def test_once_cli_does_not_enter_continuous_loop(self):
        summary=BrowserCycleSummary("r",0,0,0,0,0,0,0,0)
        with mock.patch("src.browser_worker.BrowserWorker.run_once",return_value=summary),mock.patch("src.browser_worker.run_continuously") as continuous,mock.patch("sys.stdout",new=StringIO()):
            from src.browser_worker import main
            self.assertEqual(main(["--once"]),0);continuous.assert_not_called()

    def test_crawl_frequency_skips_not_yet_due_source(self):
        now=datetime.now(UTC)
        self.assertFalse(source_is_due({"last_crawl":now.isoformat(),"crawl_frequency_minutes":60},now=now))
        self.assertTrue(source_is_due({"last_crawl":"","crawl_frequency_minutes":60},now=now))


class GeminiFallbackTests(unittest.TestCase):
    class Models:
        def __init__(self,code):self.code=code;self.calls=[]
        def generate_content(self,**kwargs):
            self.calls.append(kwargs["model"])
            if len(self.calls)==1:
                exc=type("ServerError",(Exception,),{})();exc.code=self.code;exc.status="UNAVAILABLE" if self.code==503 else "ERROR";raise exc
            return SimpleNamespace(text="ok")

    def test_model_selection_prefers_37_then_36(self):
        self.assertEqual(select_production_flash_model(["models/gemini-3.6-flash","gemini-3.7-flash"]),PRIMARY_GEMINI_MODEL)

    def test_503_uses_one_fallback(self):
        models=self.Models(503);result=generate_with_availability_fallback(SimpleNamespace(models=models),contents="x",config=None)
        self.assertEqual(models.calls,[PRIMARY_GEMINI_MODEL,AVAILABILITY_FALLBACK_MODEL]);self.assertEqual(result.attempts,2)

    def test_non_503_never_falls_back(self):
        for code in (400,401,403,404,429):
            with self.subTest(code=code):
                models=self.Models(code)
                with self.assertRaises(GeminiProviderError):generate_with_availability_fallback(SimpleNamespace(models=models),contents="x",config=None)
                self.assertEqual(models.calls,[PRIMARY_GEMINI_MODEL])


if __name__=="__main__":unittest.main()
