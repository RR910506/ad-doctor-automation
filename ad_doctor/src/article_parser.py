from __future__ import annotations

from dataclasses import asdict, dataclass
from html import unescape
import json
import re
import time
import warnings
from typing import Any
from urllib.parse import urljoin, urlparse


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)

BLOCKED_PATH_TOKENS = {
    "about",
    "advertise",
    "advertising",
    "author",
    "authors",
    "category",
    "contact",
    "cookies",
    "jobs",
    "login",
    "newsletter",
    "privacy",
    "resources",
    "search",
    "subscribe",
    "tag",
    "tags",
    "terms",
    "topics",
    "whitepaper",
    "whitepapers",
}

GENERIC_TITLE_PATTERNS = (
    r"\bhomepage\b",
    r"\bhome page\b",
    r"\bleading source\b",
    r"\bmarketing whitepapers?\b",
    r"\bmarketing news, voices and jobs\b",
    r"\bdigital marketing news\b",
    r"\bin-depth journalism covering\b",
    r"\bfind the latest\b",
    r"\bprivacy policy\b",
    r"\bterms (?:of|and)\b",
    r"\bsubscribe\b",
    r"\bnewsletter\b",
    r"\bjobs\b",
)


@dataclass(frozen=True)
class ParsedArticle:
    article_url: str
    headline: str | None
    published_date: str | None
    author: str | None
    meta_description: str | None
    article_text: str | None

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def parse_article_url(article_url: str, timeout: int = 10) -> ParsedArticle:
    response = request_with_retries(
        article_url,
        timeout=timeout,
        headers=build_request_headers(referer=root_from_url(article_url)),
    )
    return parse_article_html(article_url, response.text)


def parse_article_html(article_url: str, html: str, fallback_headline: str | None = None) -> ParsedArticle:
    headline = fallback_headline
    meta_description = None
    author = None
    published_date = None
    article_text = extract_with_trafilatura(html, article_url)

    soup = build_soup(html)
    if soup is not None:
        structured_data = extract_structured_article_fields(soup)
        headline = first_text(
            [
                structured_data.get("headline"),
                get_meta_content(soup, "og:title"),
                get_meta_content(soup, "twitter:title"),
                soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else None,
                soup.title.string if soup.title and soup.title.string else None,
                fallback_headline,
            ]
        )
        meta_description = first_text(
            [
                structured_data.get("description"),
                get_meta_content(soup, "description", attribute_name="name"),
                get_meta_content(soup, "og:description"),
                get_meta_content(soup, "twitter:description"),
            ]
        )
        author = first_text(
            [
                structured_data.get("author"),
                get_meta_content(soup, "author", attribute_name="name"),
                get_meta_content(soup, "article:author"),
                get_meta_content(soup, "byl"),
            ]
        )
        published_date = first_text(
            [
                structured_data.get("published_date"),
                get_meta_content(soup, "article:published_time"),
                get_meta_content(soup, "datePublished", attribute_name="itemprop"),
                get_meta_content(soup, "pubdate", attribute_name="name"),
                get_meta_content(soup, "date", attribute_name="name"),
            ]
        )

        if not article_text:
            article_text = structured_data.get("article_text")
        if not article_text:
            article_text = extract_text_with_soup(soup)
    else:
        headline = first_text([extract_title_with_regex(html), fallback_headline])
        meta_description = extract_meta_with_regex(html, "description")
        author = extract_meta_with_regex(html, "author")
        published_date = extract_meta_property_with_regex(html, "article:published_time")
        if not article_text:
            article_text = extract_paragraphs_with_regex(html)

    if not headline:
        headline = fallback_headline

    return ParsedArticle(
        article_url=article_url,
        headline=clean_text(headline),
        published_date=clean_text(published_date),
        author=clean_text(author),
        meta_description=clean_text(meta_description),
        article_text=clean_text(article_text),
    )


def extract_article_links(source_url: str, html: str, limit: int | None = 10) -> list[str]:
    return [
        candidate["article_url"]
        for candidate in extract_article_link_candidates(source_url, html, limit=limit)
    ]


def extract_article_link_candidates(source_url: str, html: str, limit: int | None = 10) -> list[dict[str, str | None]]:
    soup = build_soup(html)
    if soup is None:
        return extract_article_link_candidates_with_regex(source_url, html, limit=limit)

    candidates: dict[str, dict[str, str | None]] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        absolute_url = normalize_article_url(urljoin(source_url, href))
        if not absolute_url:
            continue
        anchor_text = clean_text(anchor.get_text(" ", strip=True)) or ""
        if is_likely_article_url(absolute_url, anchor_text, source_url=source_url):
            candidates[absolute_url] = {
                "article_url": absolute_url,
                "headline": anchor_text or None,
                "published_date": None,
            }
        if limit is not None and len(candidates) >= limit:
            break
    if limit is None or len(candidates) < limit:
        for embedded_url in extract_embedded_article_urls(source_url, html):
            if embedded_url in candidates:
                continue
            headline = headline_from_url(embedded_url)
            if is_likely_article_url(embedded_url, headline or "", source_url=source_url):
                candidates[embedded_url] = {
                    "article_url": embedded_url,
                    "headline": headline,
                    "published_date": None,
                }
            if limit is not None and len(candidates) >= limit:
                break
    values = list(candidates.values())
    return values if limit is None else values[:limit]


def extract_link_candidates(source_url: str, html: str, limit: int | None = None) -> list[dict[str, str | None]]:
    soup = build_soup(html)
    if soup is None:
        return extract_link_candidates_with_regex(source_url, html, limit=limit)

    candidates: dict[str, dict[str, str | None]] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        absolute_url = normalize_article_url(urljoin(source_url, href))
        if not absolute_url or not is_same_domain_url(source_url, absolute_url):
            continue
        anchor_text = clean_text(anchor.get_text(" ", strip=True)) or ""
        candidates[absolute_url] = {"url": absolute_url, "text": anchor_text or None}
        if limit is not None and len(candidates) >= limit:
            break
    if limit is None or len(candidates) < limit:
        for embedded_url in extract_embedded_article_urls(source_url, html):
            if embedded_url in candidates:
                continue
            headline = headline_from_url(embedded_url)
            if is_likely_article_url(embedded_url, headline or "", source_url=source_url):
                candidates[embedded_url] = {
                    "url": embedded_url,
                    "text": headline,
                }
            if limit is not None and len(candidates) >= limit:
                break
    values = list(candidates.values())
    return values if limit is None else values[:limit]


def extract_link_candidates_with_regex(source_url: str, html: str, limit: int | None = None) -> list[dict[str, str | None]]:
    candidates: dict[str, dict[str, str | None]] = {}
    anchor_pattern = re.compile(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    href_pattern = re.compile(r"href=[\"'](?P<href>[^\"']+)[\"']", flags=re.IGNORECASE)
    for match in anchor_pattern.finditer(html):
        href_match = href_pattern.search(match.group("attrs"))
        if not href_match:
            continue
        absolute_url = normalize_article_url(urljoin(source_url, href_match.group("href").strip()))
        if not absolute_url or not is_same_domain_url(source_url, absolute_url):
            continue
        anchor_text = clean_html_fragment(match.group("body")) or ""
        candidates[absolute_url] = {"url": absolute_url, "text": anchor_text or None}
        if limit is not None and len(candidates) >= limit:
            break
    values = list(candidates.values())
    return values if limit is None else values[:limit]


def extract_article_link_candidates_with_regex(source_url: str, html: str, limit: int | None = 10) -> list[dict[str, str | None]]:
    candidates: dict[str, dict[str, str | None]] = {}
    anchor_pattern = re.compile(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    href_pattern = re.compile(r"href=[\"'](?P<href>[^\"']+)[\"']", flags=re.IGNORECASE)
    for match in anchor_pattern.finditer(html):
        href_match = href_pattern.search(match.group("attrs"))
        if not href_match:
            continue
        absolute_url = normalize_article_url(urljoin(source_url, href_match.group("href").strip()))
        if not absolute_url:
            continue
        anchor_text = clean_html_fragment(match.group("body")) or ""
        if is_likely_article_url(absolute_url, anchor_text, source_url=source_url):
            candidates[absolute_url] = {
                "article_url": absolute_url,
                "headline": anchor_text or None,
                "published_date": None,
            }
        if limit is not None and len(candidates) >= limit:
            break
    values = list(candidates.values())
    return values if limit is None else values[:limit]


def discover_feed_links(source_url: str, html: str, limit: int | None = 5) -> list[str]:
    feed_links = discover_feed_links_with_regex(source_url, html)
    soup = build_soup(html)
    if soup is None:
        return feed_links if limit is None else feed_links[:limit]

    for tag in soup.find_all("link", href=True):
        rel_values = tag.get("rel") or []
        type_value = str(tag.get("type") or "").lower()
        if isinstance(rel_values, str):
            rel_values = [rel_values]
        rel_text = " ".join(str(value).lower() for value in rel_values)
        if "alternate" in rel_text and ("rss" in type_value or "atom" in type_value or "xml" in type_value):
            feed_links.append(urljoin(source_url, str(tag["href"])))

    for anchor in soup.find_all("a", href=True):
        text = clean_text(anchor.get_text(" ", strip=True)) or ""
        href = str(anchor.get("href", "")).strip()
        if re.search(r"\b(rss|feed|atom)\b", f"{text} {href}", flags=re.IGNORECASE):
            feed_links.append(urljoin(source_url, href))

    deduped = list(dict.fromkeys(feed_links))
    return deduped if limit is None else deduped[:limit]


def discover_feed_links_with_regex(source_url: str, html: str) -> list[str]:
    feed_links: list[str] = []
    for match in re.finditer(r"<link\b[^>]+>", html, flags=re.IGNORECASE):
        tag_text = match.group(0)
        if not re.search(r'rel=["\'][^"\']*alternate', tag_text, flags=re.IGNORECASE):
            continue
        if not re.search(r'type=["\'][^"\']*(rss|atom|xml)', tag_text, flags=re.IGNORECASE):
            continue
        href_match = re.search(r'href=["\']([^"\']+)["\']', tag_text, flags=re.IGNORECASE)
        if href_match:
            feed_links.append(urljoin(source_url, href_match.group(1)))
    return feed_links


def fetch_homepage_html(source_url: str, timeout: int = 10) -> str:
    response = request_with_retries(
        source_url,
        timeout=timeout,
        headers=build_request_headers(referer=root_from_url(source_url)),
    )
    return response.text


def build_request_headers(referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def require_requests():
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install the requests package to fetch articles.") from exc
    return requests


def request_with_retries(
    url: str,
    timeout: int = 10,
    headers: dict[str, str] | None = None,
    attempts: int = 3,
):
    requests = require_requests()
    retryable_status_codes = {408, 425, 429, 500, 502, 503, 504}
    last_exception: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                timeout=timeout,
                headers=headers or build_request_headers(referer=root_from_url(url)),
                allow_redirects=True,
            )
            if (
                response.status_code in retryable_status_codes
                and attempt < attempts - 1
            ):
                time.sleep(retry_delay_seconds(response, attempt))
                continue
            response.raise_for_status()
            return response
        except Exception as exc:
            last_exception = exc
            if attempt >= attempts - 1:
                break
            time.sleep(0.6 * (2**attempt))
    raise last_exception or RuntimeError(f"Failed to fetch {url}")


def retry_delay_seconds(response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 8.0)
        except ValueError:
            pass
    return min(0.75 * (2**attempt), 6.0)


def build_soup(html: str):
    try:
        from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
    except ImportError:
        return None
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
    return BeautifulSoup(html, "html.parser")


def extract_with_trafilatura(html: str, article_url: str) -> str | None:
    try:
        import trafilatura
    except ImportError:
        return None
    return trafilatura.extract(html, url=article_url, include_comments=False, include_tables=False)


def extract_structured_article_fields(soup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for script in soup.find_all("script"):
        script_id = str(script.get("id") or "")
        script_type = str(script.get("type") or "")
        if script_type != "application/ld+json" and script_id != "__NEXT_DATA__":
            continue
        raw_text = script.string or script.get_text()
        if not raw_text:
            continue
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            continue
        for candidate in iter_structured_article_nodes(payload):
            set_structured_field(
                fields,
                "headline",
                structured_value(candidate.get("headline") or candidate.get("name")),
            )
            set_structured_field(fields, "description", structured_value(candidate.get("description")))
            set_structured_field(
                fields,
                "published_date",
                structured_value(candidate.get("datePublished") or candidate.get("dateModified") or candidate.get("publishedAt")),
            )
            set_structured_field(fields, "author", structured_author(candidate.get("author")))
            set_structured_field(fields, "article_text", structured_value(candidate.get("articleBody") or candidate.get("body")))
    return {key: value for key, value in fields.items() if value}


def set_structured_field(fields: dict[str, str], key: str, value: str | None) -> None:
    if value and not fields.get(key):
        fields[key] = value


def iter_structured_article_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from iter_structured_article_nodes(item)
        return
    if not isinstance(value, dict):
        return
    type_value = value.get("@type") or value.get("type")
    type_values = type_value if isinstance(type_value, list) else [type_value]
    type_text = " ".join(str(item).lower() for item in type_values if item)
    article_types = ("article", "newsarticle", "blogposting", "reportageNewsArticle".lower())
    if any(article_type in type_text for article_type in article_types):
        yield value
    if "@graph" in value:
        yield from iter_structured_article_nodes(value["@graph"])
    for nested_key in ("props", "pageProps", "article", "post", "story", "data"):
        if nested_key in value:
            yield from iter_structured_article_nodes(value[nested_key])


def structured_value(value) -> str | None:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return structured_value(value.get("name") or value.get("text") or value.get("title"))
    if isinstance(value, list):
        values = [structured_value(item) for item in value]
        return first_text([item for item in values if item])
    return None


def structured_author(value) -> str | None:
    if isinstance(value, list):
        authors = [structured_author(item) for item in value]
        authors = [author for author in authors if author]
        return ", ".join(authors) if authors else None
    return structured_value(value)


def get_meta_content(soup, value: str, attribute_name: str = "property") -> str | None:
    tag = soup.find("meta", attrs={attribute_name: value})
    if tag and tag.get("content"):
        return str(tag.get("content"))
    return None


def extract_text_with_soup(soup) -> str | None:
    for unwanted in soup(["script", "style", "nav", "footer", "header", "aside"]):
        unwanted.decompose()
    article = soup.find("article") or soup.find("main") or soup.body
    if not article:
        return None
    paragraphs = [clean_text(paragraph.get_text(" ", strip=True)) for paragraph in article.find_all("p")]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph and len(paragraph) > 40]
    if paragraphs:
        return "\n\n".join(paragraphs)
    text = clean_text(article.get_text(" ", strip=True))
    return text if text and len(text) > 100 else None


def extract_title_with_regex(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    return clean_html_fragment(match.group(1)) if match else None


def extract_meta_with_regex(html: str, name: str) -> str | None:
    pattern = rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']'
    match = re.search(pattern, html, flags=re.IGNORECASE)
    return clean_html_fragment(match.group(1)) if match else None


def extract_meta_property_with_regex(html: str, property_name: str) -> str | None:
    pattern = rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)["\']'
    match = re.search(pattern, html, flags=re.IGNORECASE)
    return clean_html_fragment(match.group(1)) if match else None


def extract_paragraphs_with_regex(html: str) -> str | None:
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = [clean_html_fragment(paragraph) for paragraph in paragraphs]
    cleaned = [paragraph for paragraph in cleaned if paragraph and len(paragraph) > 30]
    return "\n\n".join(cleaned) if cleaned else None


def clean_html_fragment(value: str) -> str | None:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return clean_text(without_tags)


def first_text(values: list[str | None]) -> str | None:
    for value in values:
        cleaned = clean_text(value)
        if cleaned:
            return cleaned
    return None


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = unescape(str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def normalize_article_url(url: str) -> str | None:
    if not url.startswith(("http://", "https://")):
        return None
    blocked_extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".pdf",
        ".mp4",
        ".mp3",
        ".zip",
    )
    cleaned = url.split("#", 1)[0].rstrip("/")
    if cleaned.lower().endswith(blocked_extensions):
        return None
    return cleaned


def extract_embedded_article_urls(source_url: str, html: str) -> list[str]:
    source_root = root_from_url(source_url)
    unescaped_html = html.replace("\\/", "/").replace("\\u002F", "/")
    urls: list[str] = []
    for match in re.finditer(r"https?://[^\"'<>\\\s]+", unescaped_html):
        url = normalize_article_url(match.group(0).rstrip(".,);]"))
        if not url or not is_same_domain_url(source_root, url):
            continue
        urls.append(url)
    return list(dict.fromkeys(urls))


def headline_from_url(url: str) -> str | None:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    if slug.isdigit():
        parts = [part for part in urlparse(url).path.rstrip("/").split("/") if part]
        slug = parts[-2] if len(parts) >= 2 else slug
    if not slug:
        return None
    return " ".join(part for part in re.split(r"[-_]+", slug) if part).strip() or None


def root_from_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def is_same_domain_url(source_url: str, candidate_url: str) -> bool:
    source_netloc = urlparse(source_url).netloc.lower().removeprefix("www.")
    candidate_netloc = urlparse(candidate_url).netloc.lower().removeprefix("www.")
    return bool(source_netloc and candidate_netloc and source_netloc == candidate_netloc)


def is_likely_article_url(url: str, anchor_text: str = "", source_url: str | None = None) -> bool:
    lowered = url.lower()
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if not path_parts:
        return False
    if source_url:
        if parsed.netloc and not is_same_domain_url(source_url, url):
            return False
    if any(part.lower() in BLOCKED_PATH_TOKENS for part in path_parts):
        return False
    if any(token in lowered for token in ("/tag/", "/category/", "/author/", "/privacy", "/terms", "/jobs", "/search")):
        return False
    if anchor_text and is_generic_headline(anchor_text):
        return False
    slug = path_parts[-1]
    meaningful_slug = path_parts[-2] if slug.isdigit() and len(path_parts) >= 2 else slug
    if len(meaningful_slug) < 8 or meaningful_slug.lower() in BLOCKED_PATH_TOKENS:
        return False
    if re.search(r"/20\d{2}/|/news/|/article/|/story/|/opinion/|/work/|/campaign|/creative|/marketing|/brand|/advertis", lowered):
        return True
    return len(path_parts) >= 2 and len(anchor_text.split()) >= 5


def is_valid_parsed_article(article: ParsedArticle) -> bool:
    headline = article.headline or ""
    if is_generic_headline(headline):
        return False
    if not is_likely_article_url(article.article_url, headline):
        return False
    if article.article_text and len(article.article_text.split()) >= 40:
        return True
    return bool(headline and len(headline.split()) >= 5)


def is_generic_headline(value: object) -> bool:
    text = clean_text(value) or ""
    lowered = text.lower()
    if not text:
        return True
    if len(text.split()) < 4:
        return True
    return any(re.search(pattern, lowered) for pattern in GENERIC_TITLE_PATTERNS)


def parse_article() -> None:
    raise NotImplementedError("Use parse_article_url or parse_article_html.")
