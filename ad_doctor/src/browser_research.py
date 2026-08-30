from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Iterable, Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
import feedparser
import requests
import trafilatura


TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


@dataclass(frozen=True, slots=True)
class CrawlControls:
    max_pages_per_source_per_cycle: int = 5
    max_pages_per_cycle: int = 50
    browser_concurrency: int = 2
    http_timeout_seconds: float = 12.0
    browser_timeout_seconds: float = 20.0
    domain_request_delay_seconds: float = 0.5
    retry_backoff_seconds: float = 2.0
    failure_circuit_breaker: int = 3
    minimum_article_characters: int = 300

    @classmethod
    def from_environment(cls) -> "CrawlControls":
        def integer(name: str, default: int) -> int:
            try:
                return max(1, int(os.getenv(name, str(default))))
            except ValueError:
                return default

        def number(name: str, default: float) -> float:
            try:
                return max(0.0, float(os.getenv(name, str(default))))
            except ValueError:
                return default

        return cls(
            max_pages_per_source_per_cycle=integer("AD_DOCTOR_MAX_PAGES_PER_SOURCE", 5),
            max_pages_per_cycle=integer("AD_DOCTOR_MAX_PAGES_PER_CYCLE", 50),
            browser_concurrency=integer("AD_DOCTOR_BROWSER_CONCURRENCY", 2),
            http_timeout_seconds=number("AD_DOCTOR_HTTP_TIMEOUT_SECONDS", 12),
            browser_timeout_seconds=number("AD_DOCTOR_BROWSER_TIMEOUT_SECONDS", 20),
            domain_request_delay_seconds=number("AD_DOCTOR_DOMAIN_DELAY_SECONDS", 0.5),
            retry_backoff_seconds=number("AD_DOCTOR_RETRY_BACKOFF_SECONDS", 2),
            failure_circuit_breaker=integer("AD_DOCTOR_FAILURE_CIRCUIT_BREAKER", 3),
        )


@dataclass(frozen=True, slots=True)
class BrowserPage:
    url: str
    html: str
    status_code: int = 200


@dataclass(frozen=True, slots=True)
class ResearchDocument:
    source_url: str
    canonical_url: str
    page_title: str
    publication: str
    publisher_network: str
    retrieved_at: str
    text: str
    author: str | None
    published_at: str | None
    extraction_method: str
    content_hash: str
    http_status: int
    provenance: str = "browser_research_engine"


class BrowserService(Protocol):
    def fetch(self, url: str, *, timeout_seconds: float) -> BrowserPage: ...


class HttpService(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> BrowserPage: ...


class RequestsHttpService:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "AdDoctorResearchBot/1.0 (+approved-sources-only)"})

    def get(self, url: str, *, timeout_seconds: float) -> BrowserPage:
        response = self.session.get(url, timeout=timeout_seconds, allow_redirects=True)
        response.raise_for_status()
        return BrowserPage(response.url, response.text, int(response.status_code))


class PlaywrightBrowserService:
    """Portable Chromium renderer. Import is deferred until a fallback is required."""

    def __init__(self, *, headless: bool = True) -> None:
        self.headless = headless

    def fetch(self, url: str, *, timeout_seconds: float) -> BrowserPage:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            try:
                page = browser.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
                page.wait_for_load_state("networkidle", timeout=int(timeout_seconds * 1000))
                return BrowserPage(page.url, page.content(), int(response.status if response else 200))
            finally:
                browser.close()


def canonicalize_url(url: str, *, base_url: str | None = None) -> str:
    absolute = urljoin(base_url or url, url)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Only absolute HTTP(S) URLs are supported")
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunparse((parsed.scheme.lower(), host + port, path.rstrip("/") or "/", "", urlencode(query), ""))


def publisher_network_for(source_name: str, explicit: str | None = None) -> str:
    if str(explicit or "").strip():
        return str(explicit).strip()
    name = re.sub(r"\s+", " ", str(source_name or "").strip())
    if name.lower().startswith("campaign ") or name.lower() == "campaign":
        return "Campaign"
    return name


def approved_domain(url: str, source: dict[str, object]) -> bool:
    target = (urlparse(url).hostname or "").lower().removeprefix("www.")
    configured = str(source.get("domain") or urlparse(str(source.get("website_url") or "")).hostname or "").lower().removeprefix("www.")
    return bool(target and configured and (target == configured or target.endswith("." + configured)))


class DomainRateLimiter:
    def __init__(self, delay_seconds: float, clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self.clock = clock
        self.sleeper = sleeper
        self._last_request: dict[str, float] = {}

    def wait(self, url: str) -> None:
        domain = (urlparse(url).hostname or "").lower()
        now = self.clock()
        remaining = self.delay_seconds - (now - self._last_request.get(domain, now - self.delay_seconds))
        if remaining > 0:
            self.sleeper(remaining)
        self._last_request[domain] = self.clock()


class FailureCircuitBreaker:
    def __init__(self, threshold: int) -> None:
        self.threshold = max(1, threshold)
        self.failures: dict[str, int] = {}

    def allow(self, domain: str) -> bool:
        return self.failures.get(domain, 0) < self.threshold

    def record_success(self, domain: str) -> None:
        self.failures[domain] = 0

    def record_failure(self, domain: str) -> None:
        self.failures[domain] = self.failures.get(domain, 0) + 1


class ArticleRetrievalService:
    def __init__(self, *, http: HttpService | None = None, browser: BrowserService | None = None, controls: CrawlControls | None = None, limiter: DomainRateLimiter | None = None, breaker: FailureCircuitBreaker | None = None) -> None:
        self.controls = controls or CrawlControls.from_environment()
        self.http = http or RequestsHttpService()
        self.browser = browser or PlaywrightBrowserService(headless=True)
        self.limiter = limiter or DomainRateLimiter(self.controls.domain_request_delay_seconds)
        self.breaker = breaker or FailureCircuitBreaker(self.controls.failure_circuit_breaker)

    def retrieve(self, url: str, source: dict[str, object]) -> ResearchDocument:
        url = canonicalize_url(url)
        if not approved_domain(url, source):
            raise PermissionError("URL is outside the approved source domain")
        domain = (urlparse(url).hostname or "").lower()
        if not self.breaker.allow(domain):
            raise RuntimeError("Source circuit breaker is open")
        self.limiter.wait(url)
        try:
            page = self.http.get(url, timeout_seconds=self.controls.http_timeout_seconds)
            document = self._extract(page, source, "http")
            if self._incomplete(document):
                if str(source.get("browser_policy") or "fallback").lower() == "never":
                    raise ValueError("HTTP article extraction is incomplete")
                page = self.browser.fetch(url, timeout_seconds=self.controls.browser_timeout_seconds)
                document = self._extract(page, source, "browser")
            if self._incomplete(document):
                raise ValueError("Article extraction is incomplete after browser fallback")
            self.breaker.record_success(domain)
            return document
        except Exception:
            self.breaker.record_failure(domain)
            raise

    def _extract(self, page: BrowserPage, source: dict[str, object], method: str) -> ResearchDocument:
        soup = BeautifulSoup(page.html, "html.parser")
        canonical_tag = soup.find("link", rel=lambda value: value and "canonical" in value)
        canonical = canonicalize_url(str(canonical_tag.get("href")) if canonical_tag and canonical_tag.get("href") else page.url, base_url=page.url)
        title_tag = soup.find("meta", property="og:title")
        title = str(title_tag.get("content") if title_tag and title_tag.get("content") else soup.title.string if soup.title and soup.title.string else "").strip()
        author_tag = soup.find("meta", attrs={"name": re.compile("author", re.I)})
        date_tag = soup.find("meta", property=re.compile(r"article:published_time", re.I)) or soup.find("time")
        author = str(author_tag.get("content") or "").strip() if author_tag else None
        published = str(date_tag.get("content") or date_tag.get("datetime") or "").strip() if date_tag else None
        extracted = trafilatura.extract(page.html, include_comments=False, include_tables=False) or ""
        if not extracted:
            article = soup.find("article") or soup.find("main") or soup.body
            extracted = article.get_text(" ", strip=True) if article else ""
        text = re.sub(r"\s+", " ", extracted).strip()
        return ResearchDocument(
            source_url=page.url,
            canonical_url=canonical,
            page_title=title,
            publication=str(source.get("source_name") or ""),
            publisher_network=publisher_network_for(str(source.get("source_name") or ""), str(source.get("publisher_network") or "") or None),
            retrieved_at=datetime.now(UTC).isoformat(),
            text=text,
            author=author,
            published_at=published,
            extraction_method=method,
            content_hash=sha256(text.encode("utf-8")).hexdigest(),
            http_status=page.status_code,
        )

    def _incomplete(self, document: ResearchDocument) -> bool:
        return len(document.text) < self.controls.minimum_article_characters or not document.page_title or not document.canonical_url


class DiscoveryService:
    def discover_feed(self, content: str, source: dict[str, object]) -> list[str]:
        parsed = feedparser.parse(content)
        urls = []
        for entry in parsed.entries:
            value = str(entry.get("link") or "")
            if value and approved_domain(value, source):
                urls.append(canonicalize_url(value))
        return list(dict.fromkeys(urls))

    def discover_sitemap(self, content: str, source: dict[str, object]) -> list[str]:
        soup = BeautifulSoup(content, "xml")
        urls = []
        for tag in soup.find_all("loc"):
            value = tag.get_text(strip=True)
            if value and approved_domain(value, source):
                urls.append(canonicalize_url(value))
        return list(dict.fromkeys(urls))

    def discover_category(self, content: str, base_url: str, source: dict[str, object]) -> list[str]:
        soup = BeautifulSoup(content, "html.parser")
        urls = []
        for anchor in soup.select("a[href]"):
            value = urljoin(base_url, str(anchor.get("href") or ""))
            if approved_domain(value, source):
                urls.append(canonicalize_url(value))
        return list(dict.fromkeys(urls))


class CrawlMemoryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def unchanged(self, document: ResearchDocument) -> bool:
        row = self.connection.execute("SELECT content_hash FROM browser_crawl_memory WHERE canonical_url=?", (document.canonical_url,)).fetchone()
        return bool(row and row[0] == document.content_hash)

    def record(self, source_id: int, document: ResearchDocument, *, status: str = "success") -> None:
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            """INSERT INTO browser_crawl_memory(source_id,url,canonical_url,first_seen,last_seen,last_fetched,content_hash,http_status,extraction_method,extraction_status,published_at,author,publisher_network)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(canonical_url) DO UPDATE SET last_seen=excluded.last_seen,last_fetched=excluded.last_fetched,content_hash=excluded.content_hash,http_status=excluded.http_status,extraction_method=excluded.extraction_method,extraction_status=excluded.extraction_status,published_at=excluded.published_at,author=excluded.author,publisher_network=excluded.publisher_network""",
            (source_id, document.source_url, document.canonical_url, now, now, now, document.content_hash, document.http_status, document.extraction_method, status, document.published_at, document.author, document.publisher_network),
        )


class ResearchBrowserService:
    def __init__(self, retrieval: ArticleRetrievalService, *, controls: CrawlControls | None = None) -> None:
        self.retrieval = retrieval
        self.controls = controls or retrieval.controls

    def retrieve_documents(self, urls: Iterable[str], approved_sources: Iterable[dict[str, object]]) -> list[ResearchDocument]:
        sources = list(approved_sources)
        documents: list[ResearchDocument] = []
        per_source: dict[int, int] = {}
        for url in urls:
            if len(documents) >= self.controls.max_pages_per_cycle:
                break
            source = next((item for item in sources if approved_domain(url, item)), None)
            if source is None:
                continue
            source_id = int(source.get("id") or 0)
            if per_source.get(source_id, 0) >= self.controls.max_pages_per_source_per_cycle:
                continue
            documents.append(self.retrieval.retrieve(url, source))
            per_source[source_id] = per_source.get(source_id, 0) + 1
        return documents

    @staticmethod
    def as_provider_documents(documents: Iterable[ResearchDocument]) -> list[dict[str, object]]:
        return [
            {
                "article_url": item.canonical_url,
                "headline": item.page_title,
                "source_name": item.publication,
                "publisher_network": item.publisher_network,
                "article_text": item.text,
                "author": item.author,
                "published_date": item.published_at,
                "retrieved_at": item.retrieved_at,
                "extraction_method": item.extraction_method,
                "content_hash": item.content_hash,
                "provenance": item.provenance,
            }
            for item in documents
        ]

    @staticmethod
    def store_for_case(connection: sqlite3.Connection, case_id: str, documents: Iterable[ResearchDocument]) -> int:
        inserted = 0
        for document in documents:
            inserted += int(connection.execute(
                """INSERT OR IGNORE INTO research_documents(case_id,source_url,canonical_url,page_title,publication,publisher_network,retrieved_at,normalized_text,content_hash,extraction_method,provenance)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (case_id, document.source_url, document.canonical_url, document.page_title, document.publication, document.publisher_network, document.retrieved_at, document.text, document.content_hash, document.extraction_method, document.provenance),
            ).rowcount)
        return inserted


def playwright_install_command() -> str:
    return "python -m playwright install --with-deps chromium"
