from __future__ import annotations

from datetime import datetime, timedelta
import html
import json
import os
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from src.config import DATABASE_PATH
from src.case_files import get_case_files_mode
from src.security.redaction import redact
from src.security.resolver import build_secret_provider
from ui.repositories import DatabaseCapabilities, UIReadRepository
from ui.view_models import (
    ArticleListItem,
    AutomationViewModel,
    CaseListItem,
    ContentStudioViewModel,
    DashboardViewModel,
    ListItem,
    LogListItem,
    ProviderHealthViewModel,
    PublishingViewModel,
    SourceListItem,
    SubscriberListItem,
    TopicQualificationItem,
)


STRUCTURED_MISSING = "Not yet generated in structured format"
QUALITY_MISSING = "Automated quality review has not yet been generated for this legacy draft."


def _json(value: object, fallback=None):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {} if fallback is None else fallback


def _text(value: object, fallback: str = "Not recorded") -> str:
    cleaned = re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()
    return cleaned or fallback


def _plain_article(value: object, limit: int = 4000) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return _text(text, "No extracted article text is stored.")[:limit]


def _list(value: object) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, dict):
        return [f"{_text(key)}: {_text(item)}" for key, item in value.items()]
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            if isinstance(item, dict):
                result.append(_text(item.get("claim") or item.get("fact") or item.get("title") or item.get("url") or json.dumps(item)))
            else:
                result.append(_text(item))
        return result
    return [_text(value)]


def _approval_status(value: object) -> str:
    status = str(value or "pending").lower()
    return {"approved": "approved", "rejected": "revision_requested", "pending": "editorial_review_pending"}.get(status, status)


class ProductionUIDataSource:
    """Page-focused production reads. It never initializes or migrates SQLite."""

    def __init__(self, database_path: str | Path = DATABASE_PATH, repository: UIReadRepository | None = None):
        self.repository = repository or UIReadRepository(database_path)
        self.database_path = self.repository.database_path
        self.capabilities: DatabaseCapabilities = self.repository.capabilities()

    def get(self, page: str):
        method = getattr(self, page, None)
        return method() if callable(method) else ()

    def list(self, page: str, **kwargs):
        method = getattr(self, f"list_{page}", None)
        if callable(method):
            return method(**kwargs)
        items = list(self.get(page) or ())
        return self._memory_page(items, **kwargs)

    @staticmethod
    def _memory_page(items, *, search="", status="All", sort="Newest first", page_number=1, page_size=10, filters=None):
        filters = filters or {}
        term = search.strip().lower()
        if term:
            items = [item for item in items if term in f"{item.title} {item.subtitle} {item.meta}".lower()]
        if status != "All":
            wanted = status.lower()
            items = [item for item in items if wanted in {item.status.lower(), item.meta.lower()} or wanted in item.meta.lower()]
        for key, value in filters.items():
            if value not in (None, "", "All"):
                items = [item for item in items if str(item.details.get(key, "")).lower() == str(value).lower()]
        if sort == "A–Z":
            items.sort(key=lambda item: item.title.lower())
        elif sort == "Oldest first":
            items.reverse()
        total = len(items)
        page_size = max(1, int(page_size))
        pages = max(1, (total + page_size - 1) // page_size)
        page_number = max(1, min(int(page_number), pages))
        start = (page_number - 1) * page_size
        return items[start:start + page_size], {"page": page_number, "page_size": page_size, "total": total, "pages": pages}

    def dashboard(self) -> DashboardViewModel | None:
        if not self.capabilities.legacy_pipeline_available:
            return None
        r = self.repository
        source_count = r.scalar("SELECT COUNT(*) FROM sources WHERE active_status=1")
        article_count = r.scalar("SELECT COUNT(*) FROM articles")
        latest_run = r.row("SELECT run_id,status,started_at,finished_at,articles_scanned FROM automation_run_history ORDER BY id DESC LIMIT 1") if r.has_table("automation_run_history") else None
        latest_fetch = r.scalar("SELECT MAX(fetch_timestamp) FROM articles", default=None)
        new_articles = 0
        if latest_run and latest_run.get("run_id"):
            new_articles = r.scalar("SELECT COUNT(*) FROM articles WHERE fetch_run_id=?", (latest_run["run_id"],))
        elif latest_fetch:
            new_articles = r.scalar("SELECT COUNT(*) FROM articles WHERE date(fetch_timestamp)=date(?)", (latest_fetch,))
        candidate = r.scalar("SELECT COUNT(*) FROM topics WHERE lower(status) NOT IN ('qualified','rejected')")
        case_count = r.scalar("SELECT COUNT(*) FROM cases") if self.capabilities.case_files_available else 0
        cases_activated = case_count > 0
        qualified = r.scalar("SELECT COUNT(*) FROM cases WHERE lower(qualification_status)='qualified' AND unique_publication_count>=3") if cases_activated else (r.scalar("SELECT COUNT(*) FROM topics t WHERE lower(t.status)='qualified' AND (SELECT COUNT(DISTINCT ts.source_id) FROM topic_sources ts JOIN sources s ON s.id=ts.source_id AND s.active_status=1 WHERE ts.topic_id=t.id)>=3") if r.has_table("topic_sources") else 0)
        awaiting = r.scalar("SELECT COUNT(*) FROM topics t WHERE lower(t.status)!='rejected' AND (SELECT COUNT(DISTINCT ts.source_id) FROM topic_sources ts JOIN sources s ON s.id=ts.source_id AND s.active_status=1 WHERE ts.topic_id=t.id)=2") if r.has_table("topic_sources") else 0
        research = r.scalar("SELECT COUNT(*) FROM research_dossiers") if r.has_table("research_dossiers") else None
        drafts = r.scalar("SELECT COUNT(*) FROM content_outputs") if r.has_table("content_outputs") else None
        pending = r.scalar("SELECT COUNT(*) FROM cases WHERE lower(COALESCE(approval_status,'pending'))='pending'") if cases_activated else (r.scalar("SELECT COUNT(*) FROM content_outputs WHERE lower(COALESCE(approval_status,'pending'))='pending'") if r.has_table("content_outputs") else None)
        failed = r.scalar("SELECT COUNT(*) FROM automation_run_history WHERE lower(status) IN ('failed','timed_out')") if r.has_table("automation_run_history") else r.scalar("SELECT COUNT(*) FROM run_logs WHERE lower(event_type) IN ('error','failed')")
        settings = self.automation()
        health = {
            "Sources": "Healthy" if source_count else "Needs setup",
            "Scheduler": "Enabled" if settings and settings.enabled else "Paused",
            "Research provider": self._provider_status("research"),
            "Content provider": self._provider_status("content"),
            "Case architecture": "Shadow enabled" if self.capabilities.case_files_available and get_case_files_mode() == "shadow" else "Available" if self.capabilities.case_files_available else "Not activated",
            "Publishing": "Available" if self.capabilities.publishing_available else "Not activated",
        }
        attention = []
        if awaiting:
            attention.append(ListItem(f"{awaiting} topic(s) await a third publication", "Qualification requires three distinct approved publications.", "qualification_pending", "Review"))
        if failed:
            attention.append(ListItem(f"{failed} automation run(s) need attention", "Review the redacted logs before retrying.", "failed", "Review"))
        cases = tuple(self.cases()[:5])
        return DashboardViewModel(
            {
                "Active sources": str(source_count),
                "Articles scanned": str(article_count),
                "New articles": str(new_articles),
                "Candidate topics": str(candidate),
                "Qualified cases" if cases_activated else "Qualified topics": str(qualified),
                "Awaiting third publication": str(awaiting),
                "Research completed": "Not activated" if research is None else str(research),
                "Content drafts": "Not activated" if drafts is None else str(drafts),
                "Approval pending": "Not activated" if pending is None else str(pending),
                "Failed runs": str(failed),
            },
            tuple(attention),
            cases,
            health,
            _text((latest_run or {}).get("finished_at") or (latest_run or {}).get("started_at"), "No completed run"),
            settings.next_run if settings else "Not scheduled",
        )

    def list_sources(self, *, search="", status="All", sort="Newest first", page_number=1, page_size=10, filters=None):
        if not self.repository.has_table("sources"):
            return [], {"page": 1, "page_size": page_size, "total": 0, "pages": 1}
        where, params = ["1=1"], []
        if search.strip():
            where.append("(s.source_name LIKE ? OR s.website_url LIKE ? OR COALESCE(s.rss_url,'') LIKE ?)")
            term = f"%{search.strip()}%"; params.extend((term, term, term))
        if status != "All":
            if status.lower() in {"active", "healthy"}: where.append("s.active_status=1")
            elif status.lower() in {"inactive", "needs attention"}: where.append("s.active_status=0")
        order = "s.source_name COLLATE NOCASE" if sort == "A–Z" else "s.created_at ASC" if sort == "Oldest first" else "s.updated_at DESC, s.id DESC"
        clause = " AND ".join(where)
        select = f"""SELECT s.id,s.source_name,s.website_url,s.rss_url,s.category,s.country,s.credibility_tier,s.active_status,s.created_at,s.updated_at,COUNT(a.id) article_count,MAX(a.fetch_timestamp) last_scan FROM sources s LEFT JOIN articles a ON a.source_id=s.id WHERE {clause} GROUP BY s.id ORDER BY {order}"""
        count = f"SELECT COUNT(*) FROM sources s WHERE {clause}"
        rows, meta = self.repository.page(select, count, params, page_number=page_number, page_size=page_size)
        return [SourceListItem(_text(row["source_name"]), _text(row["website_url"]), "active" if row["active_status"] else "inactive", "Healthy" if row["active_status"] else "Inactive", {"id": row["id"], "website": row["website_url"], "rss_url": row["rss_url"], "category": row["category"] or "Not set", "country": row["country"] or "Not set", "credibility_tier": row["credibility_tier"], "created_at": row["created_at"], "updated_at": row["updated_at"], "last_scan": row["last_scan"] or "No scan recorded", "articles": int(row["article_count"] or 0), "active": bool(row["active_status"])}) for row in rows], meta

    def sources(self):
        return self.list_sources(page_size=100)[0]

    def list_articles(self, *, search="", status="All", sort="Newest first", page_number=1, page_size=10, filters=None):
        filters = filters or {}; where, params = ["1=1"], []
        if not self.repository.has_table("articles"):
            return [], {"page": 1, "page_size": page_size, "total": 0, "pages": 1}
        if search.strip():
            where.append("(a.headline LIKE ? OR a.source_name LIKE ? OR a.article_url LIKE ?)"); term=f"%{search.strip()}%";params.extend((term,term,term))
        if filters.get("publication") not in (None,"","All"):
            where.append("a.source_name=?");params.append(filters["publication"])
        if filters.get("topic_linked") not in (None,"","All"):
            where.append("ts.topic_id IS NOT NULL" if str(filters["topic_linked"]).lower() in {"yes","linked","true"} else "ts.topic_id IS NULL")
        if filters.get("date_from"):
            where.append("date(COALESCE(a.published_date,a.fetch_timestamp))>=date(?)");params.append(filters["date_from"])
        if filters.get("date_to"):
            where.append("date(COALESCE(a.published_date,a.fetch_timestamp))<=date(?)");params.append(filters["date_to"])
        if status != "All":
            if status.lower()=="extracted":where.append("COALESCE(a.article_text,'')<>''")
            elif status.lower()=="linked":where.append("ts.topic_id IS NOT NULL")
            elif status.lower()=="unlinked":where.append("ts.topic_id IS NULL")
            elif status.lower()=="warning":where.append("COALESCE(a.article_text,'')='' ")
        order="a.headline COLLATE NOCASE" if sort=="A–Z" else "COALESCE(a.published_date,a.fetch_timestamp) ASC" if sort=="Oldest first" else "COALESCE(a.published_date,a.fetch_timestamp) DESC,a.id DESC"
        clause=" AND ".join(where)
        joins="LEFT JOIN topic_sources ts ON ts.article_id=a.id LEFT JOIN topics t ON t.id=ts.topic_id"
        select=f"SELECT a.id,a.headline,a.source_name,a.source_url,a.article_url,a.published_date,a.fetch_timestamp,a.fetch_run_id,a.meta_description,a.article_text,t.exact_topic,t.id topic_id FROM articles a {joins} WHERE {clause} GROUP BY a.id ORDER BY {order}"
        count=f"SELECT COUNT(DISTINCT a.id) FROM articles a {joins} WHERE {clause}"
        rows,meta=self.repository.page(select,count,params,page_number=page_number,page_size=page_size)
        items=[]
        for row in rows:
            linked=bool(row["topic_id"]); extracted=bool(str(row["article_text"] or "").strip())
            items.append(ArticleListItem(_text(row["headline"],"Untitled article"),f"{_text(row['source_name'])} · {_text(row['published_date'] or row['fetch_timestamp'],'Date unavailable')}","extracted" if extracted else "warning","Linked" if linked else "Unlinked",{"id":row["id"],"publication":row["source_name"],"article_url":row["article_url"],"canonical_url":row["article_url"],"date":row["published_date"],"fetch_date":row["fetch_timestamp"],"fetch_run_id":row["fetch_run_id"],"extraction_status":"Extracted" if extracted else "Text unavailable","topic":row["exact_topic"] or "Not linked","topic_linked":linked,"summary":_text(row["meta_description"],"No stored summary."),"article_text":_plain_article(row["article_text"])}))
        return items,meta

    def articles(self):
        return self.list_articles(page_size=100)[0]

    def list_qualification(self, *, search="", status="All", sort="Newest first", page_number=1, page_size=10, filters=None):
        if not {"topics","topic_sources","sources","articles"}.issubset(self.repository.tables):
            return [], {"page":1,"page_size":page_size,"total":0,"pages":1}
        where=["1=1"];params=[]
        if search.strip():where.append("(t.exact_topic LIKE ? OR COALESCE(t.representative_headline,'') LIKE ?)");term=f"%{search.strip()}%";params.extend((term,term))
        clause=" AND ".join(where)
        order="q.exact_topic COLLATE NOCASE" if sort=="A–Z" else "q.topic_created_at ASC" if sort=="Oldest first" else "q.topic_created_at DESC,q.id DESC"
        grouped=f"""SELECT t.id,t.exact_topic,t.representative_headline,t.status,t.created_at topic_created_at,COUNT(DISTINCT CASE WHEN s.active_status=1 THEN s.id END) publication_count,MAX(ts.created_at) last_updated FROM topics t LEFT JOIN topic_sources ts ON ts.topic_id=t.id LEFT JOIN sources s ON s.id=ts.source_id WHERE {clause} GROUP BY t.id"""
        status_filter=""
        if status!="All":
            wanted=status.lower()
            if wanted=="qualified":status_filter=" WHERE lower(status)='qualified' AND publication_count>=3"
            elif wanted=="awaiting third publication":status_filter=" WHERE lower(status)!='rejected' AND publication_count=2"
            elif wanted=="rejected":status_filter=" WHERE lower(status)='rejected'"
        select=f"SELECT q.id,q.exact_topic,q.representative_headline,q.status,q.topic_created_at,q.publication_count,q.last_updated FROM ({grouped}) q{status_filter} ORDER BY {order}"
        count=f"SELECT COUNT(*) FROM ({grouped}) q{status_filter}"
        topics,meta=self.repository.page(select,count,params,page_number=page_number,page_size=page_size)
        items=[]
        for topic in topics:
            supporting=self.repository.rows("""SELECT DISTINCT a.headline,a.article_url,s.source_name FROM topic_sources ts JOIN sources s ON s.id=ts.source_id AND s.active_status=1 LEFT JOIN articles a ON a.id=ts.article_id WHERE ts.topic_id=? ORDER BY s.source_name""",(topic["id"],))
            count=int(topic["publication_count"] or 0);raw=str(topic["status"] or "").lower()
            if raw=="rejected":status="rejected";reason="Rejected by the qualification pipeline."
            elif count>=3 and raw=="qualified":status="qualified";reason=f"Covered by {count} distinct approved publications."
            elif count==2:status="qualification_pending";reason="Awaiting coverage from one more distinct approved publication."
            elif count==1:status="early_coverage";reason="Early coverage from one approved publication."
            elif count>=3:status="needs_review";reason=f"{count} publications are linked, but the topic still needs validity review."
            else:status="needs_review";reason="No approved publication support is linked yet."
            articles=tuple(ArticleListItem(_text(row["headline"],"Stored article"),_text(row["source_name"]),"extracted","",{"article_url":row["article_url"]}) for row in supporting)
            items.append(TopicQualificationItem(_text(topic["exact_topic"]),f"{count} distinct approved publication(s)",status,{"qualified":"Qualified","qualification_pending":"Awaiting third publication","early_coverage":"Early coverage","rejected":"Rejected","needs_review":"Needs review"}[status],{"id":topic["id"],"count":count,"publications":[row["source_name"] for row in supporting],"supporting":articles,"excluded":[],"reason":reason,"qualification_reason":reason,"rejection_reason":reason if status=="rejected" else "","case_linked":self._case_for_topic(topic["id"]) is not None}))
        return items,meta

    def qualification(self):
        return tuple(self.list_qualification(page_size=2000)[0])

    def _case_for_topic(self, topic_id):
        if not self.repository.has_table("cases"): return None
        return self.repository.row("SELECT case_id,workflow_status FROM cases WHERE topic_id=? LIMIT 1",(topic_id,))

    def cases(self):
        if self.repository.has_table("cases") and int(self.repository.scalar("SELECT COUNT(*) FROM cases")):
            rows=self.repository.rows("SELECT case_id,topic_id,canonical_topic,brand,campaign,content_type,workflow_status,qualification_status,unique_publication_count,qualification_reason,rejection_reason,priority_score,quality_status,approval_status,publication_status,updated_at,draft_id FROM cases ORDER BY updated_at DESC")
            return tuple(CaseListItem(_text(row["canonical_topic"])," · ".join(x for x in (_text(row["brand"],""),_text(row["content_type"],"")) if x),_text(row["workflow_status"]),_text(row["quality_status"]),{"case_id":row["case_id"],"topic_id":row["topic_id"],"brand":row["brand"],"content_type":row["content_type"],"sources":int(row["unique_publication_count"] or 0),"approval":row["approval_status"],"publication":row["publication_status"],"quality":row["quality_status"],"last_update":row["updated_at"],"draft_id":row["draft_id"],"data_source":"case"}) for row in rows)
        if not {"topics","locked_topics"}.issubset(self.repository.tables):return ()
        rows=self.repository.rows("""SELECT t.id topic_id,t.exact_topic,MAX(lt.id) locked_topic_id,MAX(lt.locked_at) updated_at,(SELECT COUNT(DISTINCT ts.source_id) FROM topic_sources ts JOIN sources s ON s.id=ts.source_id AND s.active_status=1 WHERE ts.topic_id=t.id) source_count,(SELECT co.id FROM content_outputs co JOIN locked_topics l2 ON l2.id=co.locked_topic_id WHERE l2.topic_id=t.id ORDER BY co.updated_at DESC,co.id DESC LIMIT 1) draft_id,(SELECT co.approval_status FROM content_outputs co JOIN locked_topics l2 ON l2.id=co.locked_topic_id WHERE l2.topic_id=t.id ORDER BY co.updated_at DESC,co.id DESC LIMIT 1) approval_status FROM topics t JOIN locked_topics lt ON lt.topic_id=t.id WHERE lower(t.status)='qualified' GROUP BY t.id ORDER BY updated_at DESC""")
        return tuple(CaseListItem(_text(row["exact_topic"]),f"{int(row['source_count'] or 0)} distinct approved publications",_approval_status(row["approval_status"]),_text(row["approval_status"],"Research available"),{"case_id":f"legacy-topic-{row['topic_id']}","topic_id":row["topic_id"],"brand":"","content_type":"Legacy editorial draft","sources":int(row["source_count"] or 0),"approval":row["approval_status"] or "pending","publication":"Not activated","quality":"Not generated","last_update":row["updated_at"],"draft_id":row["draft_id"],"locked_topic_id":row["locked_topic_id"],"data_source":"legacy_projection"}) for row in rows)

    def content_studio(self):
        queue=tuple(item for item in self.cases() if item.details.get("draft_id") or item.details.get("locked_topic_id"))
        if not queue:return None
        packages={item.title:self._package(item) for item in queue}
        return ContentStudioViewModel(queue,packages[queue[0].title],packages)

    def _package(self,item):
        if item.details.get("data_source")=="case" and self.capabilities.structured_content_available:
            structured=self._structured_package(item)
            if structured:return structured
        return self._legacy_package(item)

    def _structured_package(self,item):
        row=self.repository.row("SELECT content_package_id,payload_json,package_version,updated_at FROM content_packages WHERE case_id=? ORDER BY package_version DESC LIMIT 1",(item.details["case_id"],))
        if not row:return None
        payload=_json(row["payload_json"]);evidence={};review={}
        if self.capabilities.evidence_available:
            ev=self.repository.row("SELECT payload_json FROM evidence_packs WHERE case_id=? ORDER BY version DESC LIMIT 1",(item.details["case_id"],));evidence=_json(ev["payload_json"]) if ev else {}
        if self.capabilities.quality_reviews_available:
            qr=self.repository.row("SELECT payload_json,gate_status,overall_score FROM quality_reviews WHERE content_package_id=? ORDER BY review_version DESC LIMIT 1",(row["content_package_id"],));review=_json(qr["payload_json"]) if qr else {};review.update({"gate_status":qr["gate_status"],"overall_score":qr["overall_score"]} if qr else {})
        slides=[]
        for index,slide in enumerate(payload.get("carousel") or [],1):slides.append({"number":slide.get("slide_number",index),"type":slide.get("slide_type","Slide"),"headline":slide.get("headline",f"Slide {index}"),"body":slide.get("body",""),"visual_direction":slide.get("visual_direction",STRUCTURED_MISSING),"source_support":"Validated Evidence Pack","sources":slide.get("supporting_source_ids") or [],"quality_warnings":[]})
        return self._package_model(item,payload,evidence,slides,review,True,[f"Package v{row['package_version']} · {row['updated_at']}"])

    def _legacy_package(self,item):
        topic_id=item.details["topic_id"]
        rows=self.repository.rows("""SELECT co.id,co.content_type,co.draft_markdown,co.approval_status,co.created_at,co.updated_at,lt.id locked_topic_id FROM content_outputs co JOIN locked_topics lt ON lt.id=co.locked_topic_id WHERE lt.topic_id=? ORDER BY co.updated_at DESC,co.id DESC""",(topic_id,)) if self.repository.has_table("content_outputs") else []
        latest_by_type={}
        for row in rows:latest_by_type.setdefault(str(row["content_type"] or "draft").lower(),row)
        locked_id=(rows[0]["locked_topic_id"] if rows else item.details.get("locked_topic_id"))
        dossier_row=self.repository.row("SELECT dossier_json,source_urls_json,updated_at FROM research_dossiers WHERE locked_topic_id=? ORDER BY id DESC LIMIT 1",(locked_id,)) if locked_id and self.repository.has_table("research_dossiers") else None
        dossier=_json((dossier_row or {}).get("dossier_json"));source_urls=_json((dossier_row or {}).get("source_urls_json"),[])
        dissection=self.repository.row("SELECT output_markdown,updated_at FROM dissection_outputs WHERE locked_topic_id=? ORDER BY id DESC LIMIT 1",(locked_id,)) if locked_id and self.repository.has_table("dissection_outputs") else None
        social=next((value for key,value in latest_by_type.items() if key in {"social","carousel","linkedin"}),None)
        blog=next((value for key,value in latest_by_type.items() if key in {"blog","article","long_form"}),None)
        fallback=rows[0] if rows else {}
        slides=self._parse_slides((social or fallback).get("draft_markdown",""))
        diagnosis=self._parse_diagnosis((dissection or {}).get("output_markdown",""))
        sources=[]
        source_names=_list(dossier.get("source_names"));articles=_list(dossier.get("source_articles"))
        for index,url in enumerate(_list(source_urls or dossier.get("source_urls"))):sources.append({"publication":source_names[index] if index<len(source_names) else "Approved publication","article":articles[index] if index<len(articles) else url,"url":url,"used_for":"Legacy research dossier"})
        research={"facts":_list(dossier.get("performance_data") or dossier.get("creative_idea") or dossier.get("execution")),"claims":_list(dossier.get("strategic_insight") or dossier.get("campaign_objective")),"contradictions":[],"unknowns":[]}
        payload={"title":item.title,"diagnostic_hook":_text(dossier.get("strategic_insight"),item.subtitle),"article":_text((blog or fallback).get("draft_markdown"),STRUCTURED_MISSING),**diagnosis,**research,"source_catalog":sources}
        versions=[f"Legacy draft {row['id']} · {row['content_type']} · {row['updated_at']}" for row in rows]
        model=self._package_model(item,payload,dossier,slides,{},False,versions)
        model["approval_content_output_id"]=(social or blog or fallback).get("id")
        model["approval_status"]=(social or blog or fallback).get("approval_status","pending")
        return model

    def _package_model(self,item,payload,evidence,slides,review,structured,versions):
        diagnosis={label:_text(payload.get(key),STRUCTURED_MISSING) for label,key in (("Symptoms","symptoms"),("Patient History","patient_history"),("Diagnosis","diagnosis"),("Treatment","treatment"),("Results","results"),("Doctor’s Opinion","doctors_opinion"),("Prescription","prescription"))}
        score=review.get("overall_score") if review else None;gate=review.get("gate_status") if review else None
        source_catalog=payload.get("source_catalog") or []
        if not source_catalog and isinstance(evidence,dict):
            for source in evidence.get("sources") or []:source_catalog.append({"publication":source.get("publication_name") or source.get("publication") or "Approved publication","article":source.get("article_title") or source.get("title") or "Stored source","url":source.get("url"),"used_for":"Validated evidence"})
        return {"title":item.title,"hook":_text(payload.get("diagnostic_hook"),item.subtitle),"status":item.status,"quality":score,"sources":item.details.get("sources",len(source_catalog)),"diagnosis":diagnosis,"slides":slides,"facts":_list(payload.get("facts") or evidence.get("facts") if isinstance(evidence,dict) else []),"claims":_list(payload.get("claims")),"contradictions":_list(evidence.get("contradictions") if isinstance(evidence,dict) else []),"unknowns":_list(evidence.get("unknowns") if isinstance(evidence,dict) else []),"source_catalog":source_catalog,"article":_text(payload.get("article"),STRUCTURED_MISSING),"quality_detail":{"gate":gate or "Not generated","dimensions":review.get("dimension_scores",{}) if review else {},"blockers":_list(review.get("blocking_findings")) if review else [],"warnings":_list(review.get("warnings")) if review else [QUALITY_MISSING],"traceability":review.get("traceability_summary",QUALITY_MISSING) if review else QUALITY_MISSING},"findings":[],"versions":versions,"quality_versions":([f"Quality review · {score}/100 · {gate}"] if review else [QUALITY_MISSING]),"timeline":[{"time":item.details.get("last_update") or "Timestamp unavailable","label":"Current editorial state","detail":item.meta}],"approval_history":[f"Current approval state: {item.details.get('approval') or 'pending'}"],"structured":structured,"case_id":item.details.get("case_id")}

    @staticmethod
    def _parse_diagnosis(markdown):
        keys={"symptoms":"symptoms","patient history":"patient_history","diagnosis":"diagnosis","treatment":"treatment","results":"results","doctor's opinion":"doctors_opinion","doctor’s opinion":"doctors_opinion","prescription":"prescription"};result={};current=None;parts=[]
        def flush():
            if current:result[current]="\n".join(parts).strip()
        for line in str(markdown or "").splitlines():
            heading=re.sub(r"^[#*\s\d.:-]+|[*:\s]+$","",line).strip().lower()
            found=next((value for label,value in keys.items() if heading==label or heading.startswith(label+" ")),None)
            if found:flush();current=found;parts=[]
            elif current:parts.append(line)
        flush()
        if not result and str(markdown or "").strip():result["diagnosis"]=str(markdown).strip()
        return result

    @staticmethod
    def _parse_slides(markdown):
        chunks=re.split(r"(?im)^#{0,4}\s*(?:slide\s*)?(\d{1,2})[\s:.-]+",str(markdown or ""));slides=[]
        if len(chunks)>2:
            for number,body in zip(chunks[1::2],chunks[2::2]):
                lines=[line.strip(" #*") for line in body.splitlines() if line.strip()];slides.append({"number":int(number),"type":"Legacy carousel","headline":lines[0] if lines else f"Slide {number}","body":"\n".join(lines[1:]) if len(lines)>1 else (lines[0] if lines else ""),"visual_direction":STRUCTURED_MISSING,"source_support":"Legacy research dossier","sources":[],"quality_warnings":[]})
        elif str(markdown or "").strip():slides=[{"number":1,"type":"Legacy carousel","headline":"Stored carousel draft","body":str(markdown).strip(),"visual_direction":STRUCTURED_MISSING,"source_support":"Legacy research dossier","sources":[],"quality_warnings":[]}]
        return slides

    def publishing(self):
        if self.capabilities.publishing_available and int(self.repository.scalar("SELECT COUNT(*) FROM publications")):
            rows=self.repository.rows("SELECT publication_id,case_id,publishing_status,google_document_url,document_title,visibility,published_at,distribution_status,updated_at FROM publications ORDER BY updated_at DESC")
            history=tuple(ListItem(_text(row["document_title"]),_text(row["visibility"]),str(row["publishing_status"]),_text(row["published_at"] or row["updated_at"]),{"case_id":row["case_id"],"google_doc_url":row["google_document_url"],"distribution":row["distribution_status"]}) for row in rows)
            ready=tuple(item for item in history if item.status not in {"published","failed"})
            blocked=tuple(item for item in history if item.status=="failed")
            return PublishingViewModel(ready,blocked,history,())
        if not self.repository.has_table("content_outputs"):return None
        rows=self.repository.rows("""SELECT co.id,co.content_type,co.approval_status,co.updated_at,t.exact_topic FROM content_outputs co JOIN locked_topics lt ON lt.id=co.locked_topic_id JOIN topics t ON t.id=lt.topic_id ORDER BY co.updated_at DESC""")
        ready=[];blocked=[]
        for row in rows:
            item=ListItem(_text(row["exact_topic"]),f"{row['content_type']} draft",_approval_status(row["approval_status"]),f"Updated {row['updated_at']}",{"content_output_id":row["id"],"publication":"Structured publishing not activated","approval":row["approval_status"],"quality":"Not generated","google_doc_url":None,"distribution":"Not activated"})
            (ready if str(row["approval_status"]).lower()=="approved" else blocked).append(item)
        return PublishingViewModel(tuple(ready),tuple(blocked),(),("Structured publishing has not yet been activated.",))

    def subscribers(self):
        if not self.capabilities.subscribers_available:return ()
        columns=self.repository.columns("subscribers");select=[name for name in ("id","email","status","consent_status","segment","frequency","last_sent_at","eligibility_status") if name in columns]
        if "email" not in select:return ()
        order_column="subscriber_id" if "subscriber_id" in columns else "id" if "id" in columns else "email"
        rows=self.repository.rows(f"SELECT {','.join(select)} FROM subscribers ORDER BY {order_column} DESC")
        def mask(email):
            local,sep,domain=str(email or "").partition("@");return (local[:1]+"***@"+domain) if sep else "***"
        return tuple(SubscriberListItem(mask(row.get("email")),f"{row.get('segment') or 'General'} · {row.get('frequency') or 'Not set'}",str(row.get("status") or "unknown"),str(row.get("consent_status") or "Consent not recorded"),{"consent":row.get("consent_status"),"segment":row.get("segment"),"frequency":row.get("frequency"),"last_sent":row.get("last_sent_at"),"eligibility":row.get("eligibility_status")}) for row in rows)

    def automation(self):
        if not self.repository.has_table("automation_settings"):return None
        row=self.repository.row("SELECT enabled,schedule_time,schedule_times,timezone,updated_at FROM automation_settings WHERE id=1")
        if not row:return AutomationViewModel(False,"Not scheduled","No completed run","Asia/Kolkata",(),())
        times=tuple(_json(row.get("schedule_times"),[]) or [row.get("schedule_time") or "09:00"]);timezone=str(row.get("timezone") or "Asia/Kolkata")
        history=[]
        if self.repository.has_table("automation_run_history"):
            rows=self.repository.rows("SELECT run_id,status,current_phase,current_source,articles_scanned,qualified_topics,error_message,started_at,finished_at FROM automation_run_history ORDER BY id DESC LIMIT 25")
            for item in rows:history.append(ListItem(_text(item.get("run_id"),"Scheduled run"),f"{_text(item.get('current_phase'),'No phase')} · {_text(item.get('current_source'),'No active source')}",str(item.get("status") or "unknown"),_text(item.get("finished_at") or item.get("started_at"),"No timestamp"),{"articles":item.get("articles_scanned"),"qualified":item.get("qualified_topics"),"error":_text(redact(item.get("error_message")),"") if item.get("error_message") else ""}))
        last=next((item.meta for item in history if item.status=="complete"),"No completed run")
        return AutomationViewModel(bool(row["enabled"]),self._next_run(times,timezone) if row["enabled"] else "Paused",last,timezone,times,tuple(history))

    @staticmethod
    def _next_run(times,timezone):
        try:now=datetime.now(ZoneInfo(timezone))
        except Exception:now=datetime.now()
        candidates=[]
        for value in times:
            try:
                hour,minute=(int(part) for part in str(value).split(":"));candidate=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
                if candidate<=now:candidate+=timedelta(days=1)
                candidates.append(candidate)
            except (TypeError,ValueError):continue
        return min(candidates).strftime("%Y-%m-%d %H:%M %Z") if candidates else "Not scheduled"

    def list_logs(self, *, search="", status="All", sort="Newest first", page_number=1, page_size=10, filters=None):
        if not self.repository.has_table("run_logs"):return [],{"page":1,"page_size":page_size,"total":0,"pages":1}
        filters=filters or {};where=["1=1"];params=[]
        if search.strip():where.append("(message LIKE ? OR phase LIKE ? OR run_id LIKE ?)");term=f"%{search.strip()}%";params.extend((term,term,term))
        if status!="All":
            mapped={"failed":"error","warning":"warning","success":"complete"}.get(status.lower(),status.lower());where.append("lower(event_type)=?");params.append(mapped)
        if filters.get("stage") not in (None,"","All"):where.append("phase=?");params.append(filters["stage"])
        if filters.get("date_from"):where.append("date(created_at)>=date(?)");params.append(filters["date_from"])
        if filters.get("date_to"):where.append("date(created_at)<=date(?)");params.append(filters["date_to"])
        clause=" AND ".join(where);order="created_at ASC" if sort=="Oldest first" else "created_at DESC,id DESC"
        rows,meta=self.repository.page(f"SELECT id,run_id,phase,event_type,message,created_at FROM run_logs WHERE {clause} ORDER BY {order}",f"SELECT COUNT(*) FROM run_logs WHERE {clause}",params,page_number=page_number,page_size=page_size)
        items=[]
        for row in rows:
            event=str(row["event_type"] or "").lower();status_value="failed" if event in {"error","failed"} else "warning" if event=="warning" else "success"
            safe=_text(redact(row["message"]));safe=re.sub(r"([A-Za-z0-9._%+-])[^@\s]*(@[^\s]+)",r"\1***\2",safe)
            items.append(LogListItem(safe,f"{_text(row['phase'])} · Run {_text(row['run_id'])}",status_value,_text(row["created_at"]),{"run_id":row["run_id"],"stage":row["phase"]}))
        return items,meta

    def logs(self):return self.list_logs(page_size=100)[0]

    def settings(self):
        research=self.repository.row("SELECT provider_key,model FROM research_provider_settings WHERE id=1") if self.repository.has_table("research_provider_settings") else None
        content=self.repository.row("SELECT provider_key,model,enabled FROM content_writer_settings WHERE id=1") if self.repository.has_table("content_writer_settings") else None
        email_configured=bool(self.repository.scalar("SELECT COUNT(*) FROM email_settings WHERE COALESCE(smtp_host,'')<>'' AND COALESCE(smtp_username,'')<>''",default=0)) if self.repository.has_table("email_settings") else False
        google_configured=bool(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        gemini=build_secret_provider(self.repository.database_path).resolve("GEMINI_API_KEY")
        gemini_status="connected" if gemini.configured else "not_configured";gemini_label='Configured' if gemini.configured else 'Not configured'
        items=(ProviderHealthViewModel("Gemini Research",gemini_status,f"Research provider: Gemini · Model: gemini-2.5-flash · Google Search grounding: Enabled · Gemini API: {gemini_label}",gemini.source),ProviderHealthViewModel("Gemini Content",gemini_status,f"Content provider: Gemini · Model: gemini-2.5-flash · Grounding: Disabled · Gemini API: {gemini_label}",gemini.source),ProviderHealthViewModel("Gemini Quality",gemini_status,f"Quality AI provider: Gemini · Model: gemini-2.5-flash · Grounding: Disabled · Gemini API: {gemini_label}",gemini.source),ProviderHealthViewModel("Perplexity","alternative", "Historical compatibility only; not required for production research.","Advanced"),ProviderHealthViewModel("OpenAI (optional)","alternative","Optional historical provider; not required for production.","Advanced"),ProviderHealthViewModel("Google Workspace","connected" if google_configured else "not_configured","Google Workspace is configured." if google_configured else "Google Workspace is not configured.","Environment status"),ProviderHealthViewModel("Email","connected" if email_configured else "not_configured","Email delivery is configured." if email_configured else "Email delivery is not configured.","Central settings"))
        return items

    def _provider_status(self,kind):
        items={item.name:item.status for item in self.settings()};name={"research":"Gemini Research","content":"Gemini Content","quality":"Gemini Quality"}.get(kind,"Gemini Content");return "Connected" if items[name]=="connected" else "Not configured"
