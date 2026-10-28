"""Bounded, resumable public-source collection with explicit coverage gaps."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import feedparser
import requests

from src.article_fetcher import FetchSummary
from src.article_parser import is_likely_article_url, parse_article_html
from src.browser_research import canonicalize_url
from src.db import (
    get_connection,
    initialize_database,
    list_active_sources,
    log_run_event,
    upsert_article,
)

USER_AGENT = "AdDoctorResearchBot/1.0"
MAX_BODY = 4 * 1024 * 1024
BROWSER_SLOTS = threading.BoundedSemaphore(2)
FRONTIER_STATUSES = {
    "queued",
    "retry",
    "success",
    "failed",
    "blocked",
    "paywall",
    "not_found",
    "robots_disallowed",
    "not_article",
}


def initialize_frontier(database_path):
    initialize_database(database_path)
    with closing(get_connection(database_path)) as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS collection_frontier (
                source_id INTEGER NOT NULL, url TEXT NOT NULL, kind TEXT NOT NULL,
                depth INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 10,
                status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT, last_run_id TEXT, last_error TEXT, http_status INTEGER,
                discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(source_id,url), FOREIGN KEY(source_id) REFERENCES sources(id)
            );
            CREATE TABLE IF NOT EXISTS collection_source_reports (
                run_id TEXT NOT NULL, source_id INTEGER NOT NULL, report_json TEXT NOT NULL,
                PRIMARY KEY(run_id,source_id), FOREIGN KEY(source_id) REFERENCES sources(id)
            );
            CREATE INDEX IF NOT EXISTS collection_frontier_due
                ON collection_frontier(source_id,status,next_attempt_at,priority);
        """)
        connection.commit()


def _in_scope(url, source):
    try:
        parsed = urlparse(url)
        configured = (
            (urlparse(source["website_url"]).hostname or "")
            .lower()
            .removeprefix("www.")
        )
        return (
            parsed.scheme in ("http", "https")
            and parsed.port in (None, 80, 443)
            and not parsed.username
            and not parsed.password
            and bool(configured)
            and (parsed.hostname or "").lower().removeprefix("www.") == configured
        )
    except (ValueError, TypeError):
        return False


def _article_link(url, text, source):
    if "adsoftheworld.com" in source["website_url"] and re.search(
        r"/(brands|collections|agencies|users)(/|$)", urlparse(url).path
    ):
        return False
    # An anchor labelled "Read more" must not hide an otherwise valid URL.
    return is_likely_article_url(
        url, text, source_url=source["website_url"]
    ) or is_likely_article_url(url, "", source_url=source["website_url"])


def _enqueue(
    connection, source, url, kind, depth=0, priority=10, refresh=False, run_id=""
):
    if not _in_scope(url, source):
        return False
    url = canonicalize_url(url)
    path = urlparse(url).path.lower()
    if re.search(r"\.(jpg|jpeg|png|gif|svg|webp|mp4|pdf|zip|css|js|woff2?)$", path):
        return False
    if re.search(r"/(login|signin|signup|register|account|checkout|cart)(/|$)", path):
        return False
    cursor = connection.execute(
        "INSERT OR IGNORE INTO collection_frontier(source_id,url,kind,depth,priority) VALUES(?,?,?,?,?)",
        (source["id"], url, kind, depth, priority),
    )
    connection.execute(
        "UPDATE collection_frontier SET last_seen=CURRENT_TIMESTAMP WHERE source_id=? AND url=?",
        (source["id"], url),
    )
    if refresh:
        connection.execute(
            """UPDATE collection_frontier SET status='queued',attempts=0,next_attempt_at=NULL,last_error=NULL
            WHERE source_id=? AND url=? AND status NOT IN ('queued','retry')
            AND (last_run_id IS NULL OR last_run_id!=?)""",
            (source["id"], url, run_id),
        )
    connection.commit()
    return bool(cursor.rowcount)


def _http_get(session, url, source, deadline):
    for _ in range(6):
        if not _in_scope(url, source):
            raise PermissionError("redirect_outside_approved_domain")
        policy = source.get("_robots_policy")
        if policy is not None and not policy.can_fetch(USER_AGENT, url):
            raise PermissionError("robots_disallowed")
        remaining = deadline - time.monotonic()
        if remaining < 0.25:
            raise TimeoutError("source_budget_reached")
        with session.get(
            url, timeout=min(7, remaining), allow_redirects=False, stream=True
        ) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("Location", ""))
                continue
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16384):
                if time.monotonic() >= deadline:
                    raise TimeoutError("source_budget_reached")
                body.extend(chunk)
                if len(body) > MAX_BODY:
                    raise ValueError("response_exceeds_4mb")
            return (
                response.url,
                response.status_code,
                bytes(body).decode(response.encoding or "utf-8", errors="replace"),
            )
    raise ValueError("too_many_redirects")


def _render(url, source, timeout_seconds):
    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    if not BROWSER_SLOTS.acquire(timeout=min(3, timeout_seconds)):
        raise TimeoutError("browser_concurrency_limit")
    try:
        timeout = max(0.25, timeout_seconds - (time.monotonic() - started))
        with sync_playwright() as playwright:
            options = {"headless": True, "timeout": int(timeout * 1000)}
            system = shutil.which("chromium") or shutil.which("chromium-browser")
            if not Path(playwright.chromium.executable_path).is_file() and system:
                options["executable_path"] = system
            browser = playwright.chromium.launch(**options)
            try:
                page = browser.new_page()
                denied = {"reason": None}

                def navigate(route):
                    if route.request.is_navigation_request():
                        policy = source.get("_robots_policy")
                        if not _in_scope(route.request.url, source):
                            denied["reason"] = "redirect_outside_approved_domain"
                        elif policy is not None and not policy.can_fetch(
                            USER_AGENT, route.request.url
                        ):
                            denied["reason"] = "robots_disallowed"
                        if denied["reason"]:
                            return route.abort()
                    return route.continue_()

                page.route("**/*", navigate)
                try:
                    response = page.goto(
                        url, wait_until="domcontentloaded", timeout=int(timeout * 1000)
                    )
                except Exception:
                    if denied["reason"]:
                        raise PermissionError(denied["reason"]) from None
                    raise
                page.wait_for_timeout(min(1000, int(timeout * 100)))
                if not _in_scope(page.url, source):
                    raise PermissionError("redirect_outside_approved_domain")
                html = page.content()
                if len(html.encode()) > MAX_BODY:
                    raise ValueError("response_exceeds_4mb")
                return page.url, response.status if response else 200, html
            finally:
                browser.close()
    finally:
        BROWSER_SLOTS.release()


def _access_status(html, check_paywall=True):
    if html.lstrip().startswith("<?xml"):
        return None
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if any(
        marker in title
        for marker in (
            "just a moment",
            "access denied",
            "verify you are human",
            "captcha",
        )
    ):
        return "blocked"
    if check_paywall and re.search(
        r'"isAccessibleForFree"\s*:\s*(false|"false")', html, re.I
    ):
        return "paywall"
    if check_paywall and any(
        marker in soup.get_text(" ", strip=True).lower()
        for marker in ("subscribe to continue reading", "sign in to continue reading")
    ):
        return "paywall"
    return None


def _non_article_page(html):
    types = set()
    for script in BeautifulSoup(html, "html.parser").select(
        'script[type="application/ld+json"]'
    ):
        try:
            pending = [json.loads(script.string or script.get_text())]
        except (ValueError, TypeError):
            continue
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                value = item.get("@type", [])
                types.update(
                    [value]
                    if isinstance(value, str)
                    else (
                        [v for v in value if isinstance(v, str)]
                        if isinstance(value, list)
                        else []
                    )
                )
                pending.extend(v for v in item.values() if isinstance(v, (dict, list)))
            elif isinstance(item, list):
                pending.extend(item)
    return "CollectionPage" in types and not types.intersection(
        {"Article", "NewsArticle", "BlogPosting", "Report", "AdvertiserContentArticle"}
    )


def _discover(connection, source, url, html, kind, depth, run_id, *, issues=None):
    found = 0
    section = urlparse(source["website_url"]).path.rstrip("/")
    if section and not (
        urlparse(url).path == section or urlparse(url).path.startswith(section + "/")
    ):
        if issues is not None and len(issues) < 30:
            issues.append(
                {
                    "url": url,
                    "outcome": "section_scope_requires_adapter",
                    "detail": "Discovery page redirected outside the approved publication section.",
                }
            )
        return 0
    if kind == "feed":
        for entry in feedparser.parse(html).entries:
            target = urljoin(url, str(entry.get("link") or ""))
            if _article_link(target, str(entry.get("title") or ""), source):
                found += _enqueue(
                    connection,
                    source,
                    target,
                    "article",
                    priority=2,
                    refresh=True,
                    run_id=run_id,
                )
        return found
    if kind == "sitemap":
        try:
            root = ET.fromstring(html)
        except ET.ParseError:
            return 0
        index = root.tag.rsplit("}", 1)[-1] == "sitemapindex"
        for node in root.iter():
            if node.tag.rsplit("}", 1)[-1] != "loc" or not node.text:
                continue
            target = urljoin(url, node.text.strip())
            if index or _article_link(
                target, target.rsplit("/", 1)[-1].replace("-", " "), source
            ):
                found += _enqueue(
                    connection,
                    source,
                    target,
                    "sitemap" if index else "article",
                    depth + 1,
                    4 if index else 7,
                    refresh=index,
                    run_id=run_id,
                )
        return found
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("link[href]"):
        target = urljoin(url, tag["href"])
        rel = tag.get("rel") or []
        if "alternate" in rel and any(
            t in tag.get("type", "") for t in ("rss", "atom")
        ):
            if not section or urlparse(target).path.startswith(section + "/"):
                found += _enqueue(
                    connection,
                    source,
                    target,
                    "feed",
                    priority=1,
                    refresh=True,
                    run_id=run_id,
                )
        elif "next" in rel:
            if (
                not section
                or urlparse(target).path.startswith(section + "/")
                or urlparse(target).path == section
            ):
                found += _enqueue(
                    connection,
                    source,
                    target,
                    "listing",
                    depth + 1,
                    5,
                    refresh=True,
                    run_id=run_id,
                )
    region = soup
    if section:
        region = soup.select_one(
            'main,[role="main"],#main,#content,.main-content,.site-content,.content-area'
        )
        if region is None:
            if issues is not None and len(issues) < 30:
                issues.append(
                    {
                        "url": url,
                        "outcome": "section_scope_requires_adapter",
                        "detail": "No identifiable main section content; page-wide article links were excluded.",
                    }
                )
            return found
        # The configured section owns its main story cards. Global navigation,
        # sidebars and recommended stories do not establish section membership.
        for excluded in region.select(
            'header,footer,nav,aside,[role="navigation"],[role="complementary"],'
            '[class*="related"],[id*="related"],[class*="recommend"],[id*="recommend"],'
            '[class*="most-read"],[class*="popular"],[itemprop="articleBody"]'
        ):
            if excluded.parent is not None:
                excluded.decompose()
    for anchor in region.select("a[href]"):
        target = urljoin(url, anchor["href"])
        path = urlparse(target).path.lower()
        if _article_link(target, anchor.get_text(" ", strip=True), source):
            found += _enqueue(
                connection,
                source,
                target,
                "article",
                depth + 1,
                2,
                refresh=True,
                run_id=run_id,
            )
        elif "next" in (anchor.get("rel") or []) or re.search(
            r"/(news|advertising|marketing|campaigns?|brands?|agencies|insights|category|archive|page)(/|$)",
            path,
        ):
            if (
                not section
                or path.startswith(section.lower() + "/")
                or path == section.lower()
            ):
                found += _enqueue(
                    connection,
                    source,
                    target,
                    "listing",
                    depth + 1,
                    6,
                    refresh=True,
                    run_id=run_id,
                )
    return found


def _source_cycle(
    database_path, source, run_id, budget, page_limit, *, http_get=None, renderer=None
):
    deadline = time.monotonic() + budget
    report = dict(
        source_id=source["id"],
        source_name=source["source_name"],
        website_url=source["website_url"],
        status="partial",
        completeness="unknown",
        attempted=0,
        articles_read=0,
        discovered=0,
        blocked=0,
        failed=0,
        inserted=0,
        updated=0,
        browser_fallbacks=0,
        queued=0,
        robots_disallowed=0,
        discovery_methods=[],
        issues=[],
    )
    getter, renderer = http_get or _http_get, renderer or _render
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    parsed = urlparse(source["website_url"])
    root, section = f"{parsed.scheme}://{parsed.netloc}", parsed.path.strip("/")
    report["discovery_scope"] = (
        source["website_url"] if section else "approved_publication_host"
    )
    robots = RobotFileParser()
    try:
        _, code, body = getter(session, root + "/robots.txt", source, deadline)
        if code in (401, 403, 429) or code >= 500 or _access_status(body, False):
            report.update(status="access_required", blocked=1)
            report["issues"].append(f"robots_http_{code}; source left unread")
            session.close()
            return report
        robots.parse(body.splitlines() if code == 200 else [])
        delay = max(
            0.25,
            float(robots.crawl_delay(USER_AGENT) or robots.crawl_delay("*") or 0.25),
        )
    except Exception as exc:
        report.update(status="robots_unavailable")
        report["issues"].append("robots_unavailable:" + type(exc).__name__)
        session.close()
        return report
    source = dict(source, _robots_policy=robots)
    with closing(get_connection(database_path)) as connection:
        _enqueue(
            connection,
            source,
            source["website_url"],
            "listing",
            priority=0,
            refresh=True,
            run_id=run_id,
        )
        if not section:
            for url in [*(robots.site_maps() or []), root + "/sitemap.xml"]:
                _enqueue(
                    connection,
                    source,
                    url,
                    "sitemap",
                    priority=4,
                    refresh=True,
                    run_id=run_id,
                )
        feed_base = source["website_url"].rstrip("/") if section else root
        for url in [
            source.get("rss_url"),
            feed_base + "/feed/",
            feed_base + "/rss.xml",
        ]:
            if url:
                _enqueue(
                    connection,
                    source,
                    url,
                    "feed",
                    priority=1,
                    refresh=True,
                    run_id=run_id,
                )
        while time.monotonic() + 0.25 < deadline and report["attempted"] < page_limit:
            preferred = (
                "listing",
                "feed",
                "article",
                "sitemap",
                "article",
                "listing",
                "article",
            )[report["attempted"] % 7]
            row = connection.execute(
                """SELECT * FROM collection_frontier WHERE source_id=? AND status IN ('queued','retry')
                AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY CASE WHEN kind=? THEN 0 ELSE 1 END,
                priority,attempts,discovered_at,rowid LIMIT 1""",
                (source["id"], datetime.now(UTC).isoformat(), preferred),
            ).fetchone()
            if row is None:
                break
            row = dict(row)
            url, kind = row["url"], row["kind"]
            state, error, code, used_browser = "success", None, None, False
            report["attempted"] += 1
            report["discovery_methods"] = sorted(
                set(report["discovery_methods"]) | {kind}
            )
            try:
                if not robots.can_fetch(USER_AGENT, url):
                    raise PermissionError("robots_disallowed")
                final, code, html = getter(session, url, source, deadline)
                if not _in_scope(final, source):
                    raise PermissionError("redirect_outside_approved_domain")
                if code in (401, 403):
                    state = "blocked"
                elif code == 429 or code >= 500:
                    state = "retry"
                elif code in (404, 410):
                    state = "not_found"
                elif code != 200:
                    state = "failed"
                else:
                    state = _access_status(html, kind == "article") or "success"
                    if state == "success" and kind == "article":
                        article = parse_article_html(final, html)
                        if _non_article_page(html):
                            connection.execute(
                                "UPDATE collection_frontier SET kind=? WHERE source_id=? AND url=?",
                                ("listing", source["id"], url),
                            )
                            report["discovered"] += _discover(
                                connection,
                                source,
                                final,
                                html,
                                "listing",
                                row["depth"],
                                run_id,
                                issues=report["issues"],
                            )
                            state = "not_article"
                        if (
                            state == "success"
                            and len(str(article.article_text or "").strip()) < 300
                        ):
                            if (
                                str(source.get("browser_policy") or "fallback")
                                != "never"
                                and deadline - time.monotonic() > 2
                            ):
                                used_browser = True
                                report["browser_fallbacks"] += 1
                                final, code, html = renderer(
                                    url, source, min(12, deadline - time.monotonic())
                                )
                                if not _in_scope(final, source):
                                    raise PermissionError(
                                        "redirect_outside_approved_domain"
                                    )
                                state = _access_status(html) or (
                                    "success" if code == 200 else "blocked"
                                )
                                article = parse_article_html(final, html)
                        if (
                            state == "success"
                            and len(str(article.article_text or "").strip()) >= 300
                            and article.headline
                        ):
                            record = article.to_record()
                            record.update(
                                source_id=source["id"],
                                source_name=source["source_name"],
                                source_url=source["website_url"],
                                fetch_run_id=run_id,
                                content_hash=sha256(
                                    str(article.article_text).encode()
                                ).hexdigest(),
                                extraction_method="browser" if used_browser else "http",
                            )
                            report[upsert_article(connection, record)] += 1
                            report["articles_read"] += 1
                        elif state == "success":
                            state = "retry"
                    elif state == "success":
                        report["discovered"] += _discover(
                            connection,
                            source,
                            final,
                            html,
                            kind,
                            row["depth"],
                            run_id,
                            issues=report["issues"],
                        )
                        soup = (
                            BeautifulSoup(html, "html.parser")
                            if kind == "listing"
                            else None
                        )
                        if (
                            soup is not None
                            and not soup.select("a[href]")
                            and len(soup.get_text(strip=True)) < 150
                        ):
                            if (
                                str(source.get("browser_policy") or "fallback")
                                != "never"
                                and deadline - time.monotonic() > 2
                            ):
                                report["browser_fallbacks"] += 1
                                final, code, html = renderer(
                                    url, source, min(12, deadline - time.monotonic())
                                )
                                if not _in_scope(final, source):
                                    raise PermissionError(
                                        "redirect_outside_approved_domain"
                                    )
                                state = _access_status(html, False) or (
                                    "success" if code == 200 else "blocked"
                                )
                                if state == "success":
                                    report["discovered"] += _discover(
                                        connection,
                                        source,
                                        final,
                                        html,
                                        kind,
                                        row["depth"],
                                        run_id,
                                        issues=report["issues"],
                                    )
            except Exception as exc:
                if isinstance(exc, PermissionError) and str(exc) == "robots_disallowed":
                    state, error = "robots_disallowed", "robots_disallowed"
                    report["robots_disallowed"] += 1
                else:
                    state, error = "retry", type(exc).__name__
            if state in ("blocked", "paywall"):
                report["blocked"] += 1
            elif state in ("retry", "failed"):
                report["failed"] += 1
            attempts = row["attempts"] + 1
            if state != "success" and len(report["issues"]) < 30:
                report["issues"].append(
                    dict(url=url, outcome=state, http_status=code, error=error)
                )
            retry_at = (
                (
                    datetime.now(UTC)
                    + (
                        timedelta(days=1)
                        if attempts >= 3
                        else timedelta(minutes=5 * 2**attempts)
                    )
                ).isoformat()
                if state == "retry"
                else None
            )
            connection.execute(
                """UPDATE collection_frontier SET status=?,attempts=?,last_run_id=?,last_error=?,http_status=?,next_attempt_at=?
                WHERE source_id=? AND url=?""",
                (
                    state,
                    attempts,
                    run_id,
                    error or (state if state != "success" else None),
                    code,
                    retry_at,
                    source["id"],
                    url,
                ),
            )
            connection.commit()
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
        counts = {
            r["status"]: r["n"]
            for r in connection.execute(
                "SELECT status,COUNT(*) n FROM collection_frontier WHERE source_id=? GROUP BY status",
                (source["id"],),
            )
        }
        report.update(
            frontier_outcomes=counts,
            queued=counts.get("queued", 0) + counts.get("retry", 0),
            unread_known_urls=sum(
                n
                for state, n in counts.items()
                if state not in ("success", "not_article")
            ),
            status="partial" if report["articles_read"] else "no_articles_read",
            budget_reached=time.monotonic() >= deadline
            or report["attempted"] >= page_limit,
        )
    session.close()
    return report


def fetch_articles(
    *,
    database_path,
    run_id,
    source_time_budget_seconds=60,
    source_parallelism=4,
    max_articles_per_source=None,
    fetch_full_text=True,
    exhaustive_scan=True,
    progress_callback=None,
    max_pages_per_source=60,
):
    initialize_frontier(database_path)
    started = time.monotonic()
    with closing(get_connection(database_path)) as connection:
        sources = list_active_sources(connection)
    reports = []
    with ThreadPoolExecutor(max_workers=max(1, min(source_parallelism, 6))) as executor:
        tasks = {
            executor.submit(
                _source_cycle,
                database_path,
                source,
                run_id,
                source_time_budget_seconds,
                max_pages_per_source,
            ): source
            for source in sources
        }
        for task in as_completed(tasks):
            source = tasks[task]
            try:
                report = task.result()
            except Exception as exc:
                report = dict(
                    source_id=source["id"],
                    source_name=source["source_name"],
                    status="failed",
                    completeness="unknown",
                    failed=1,
                    issues=[type(exc).__name__],
                )
            reports.append(report)
            with closing(get_connection(database_path)) as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO collection_source_reports(run_id,source_id,report_json) VALUES(?,?,?)",
                    (run_id, source["id"], json.dumps(report)),
                )
                log_run_event(
                    connection,
                    run_id,
                    "publication_collection",
                    "source_complete",
                    f"{source['source_name']}: {report['status']}",
                    json.dumps(report),
                )
                connection.commit()
            if progress_callback:
                progress_callback(
                    dict(
                        event_type="source_complete",
                        source_name=source["source_name"],
                        completed_sources=len(reports),
                        total_sources=len(sources),
                    )
                )
    return FetchSummary(
        run_id=run_id,
        sources_checked=len(sources),
        articles_seen=sum(r.get("attempted", 0) for r in reports),
        inserted=sum(r.get("inserted", 0) for r in reports),
        updated=sum(r.get("updated", 0) for r in reports),
        failed=sum(r.get("failed", 0) + r.get("blocked", 0) for r in reports),
        messages=[
            f"{r['source_name']}: {r['status']}; {r.get('queued',0)} URLs queued"
            for r in reports
        ],
        duration_seconds=round(time.monotonic() - started, 2),
    )


def source_reports(database_path, run_id):
    initialize_frontier(database_path)
    with closing(get_connection(database_path)) as connection:
        return [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT report_json FROM collection_source_reports WHERE run_id=? ORDER BY source_id",
                (run_id,),
            )
        ]


def export_frontier(database_path, max_rows_per_source=None):
    initialize_frontier(database_path)
    snapshots = []
    with closing(get_connection(database_path)) as connection:
        for source in connection.execute(
            "SELECT id,website_url FROM sources"
        ).fetchall():
            total = connection.execute(
                "SELECT COUNT(*) FROM collection_frontier WHERE source_id=?",
                (source["id"],),
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT rowid AS discovery_order,url,kind,depth,priority,status,attempts,next_attempt_at,last_run_id,last_error,http_status,discovered_at,last_seen
                FROM collection_frontier WHERE source_id=? ORDER BY CASE WHEN status IN ('queued','retry') THEN 0
                WHEN kind IN ('listing','feed','sitemap') THEN 1 ELSE 2 END,discovered_at,rowid LIMIT ?""",
                (
                    source["id"],
                    -1 if max_rows_per_source is None else max_rows_per_source,
                ),
            ).fetchall()
            snapshots.append(
                dict(
                    website_url=source["website_url"],
                    rows=[dict(row) for row in rows],
                    total_urls=total,
                    overflow_urls=max(0, total - len(rows)),
                )
            )
    return dict(
        version=1,
        saved_at=datetime.now(UTC).isoformat(),
        sources=snapshots,
        overflow_urls=sum(s["overflow_urls"] for s in snapshots),
    )


def import_frontier(database_path, checkpoint):
    if not checkpoint:
        return
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != 1:
        raise ValueError("Unsupported crawl checkpoint version")
    initialize_frontier(database_path)
    with closing(get_connection(database_path)) as connection:
        sources = {
            canonicalize_url(r["website_url"]): dict(r)
            for r in connection.execute("SELECT * FROM sources").fetchall()
        }
        for snapshot in checkpoint.get("sources", []):
            try:
                source = sources.get(canonicalize_url(snapshot["website_url"]))
            except (KeyError, ValueError, TypeError):
                continue
            if not source:
                continue
            # The caller holds the global automation lock. Shared state is the
            # latest authority, including newer outcomes from another host.
            if not snapshot.get("overflow_urls"):
                connection.execute(
                    "DELETE FROM collection_frontier WHERE source_id=?", (source["id"],)
                )
            for row in sorted(
                snapshot.get("rows", []),
                key=lambda r: int(r.get("discovery_order") or 0),
            ):
                if (
                    not _in_scope(row.get("url"), source)
                    or row.get("status") not in FRONTIER_STATUSES
                    or row.get("kind") not in ("listing", "feed", "sitemap", "article")
                ):
                    continue
                connection.execute(
                    """INSERT INTO collection_frontier(source_id,url,kind,depth,priority,status,attempts,next_attempt_at,last_run_id,last_error,http_status,discovered_at,last_seen)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,url) DO UPDATE SET kind=excluded.kind,depth=excluded.depth,priority=excluded.priority,
                    status=excluded.status,attempts=excluded.attempts,next_attempt_at=excluded.next_attempt_at,last_run_id=excluded.last_run_id,
                    last_error=excluded.last_error,http_status=excluded.http_status,discovered_at=excluded.discovered_at,last_seen=excluded.last_seen""",
                    (
                        source["id"],
                        canonicalize_url(row["url"]),
                        row["kind"],
                        int(row.get("depth") or 0),
                        int(row.get("priority") or 0),
                        row["status"],
                        int(row.get("attempts") or 0),
                        row.get("next_attempt_at"),
                        row.get("last_run_id"),
                        row.get("last_error"),
                        row.get("http_status"),
                        row.get("discovered_at") or datetime.now(UTC).isoformat(),
                        row.get("last_seen") or datetime.now(UTC).isoformat(),
                    ),
                )
        connection.commit()
