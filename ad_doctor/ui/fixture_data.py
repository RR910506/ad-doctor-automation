from copy import deepcopy
from dataclasses import replace

from ui.view_models import *


def _slides():
    types = ['Diagnostic Hook', 'Symptoms', 'Patient History', 'Hidden Problem', 'Diagnosis', 'Treatment', 'What Worked', 'Risk', "Doctor’s Opinion", 'Prescription']
    return [
        {
            'number': number,
            'type': slide_type,
            'headline': slide_type,
            'body': f'One focused strategic point for {slide_type.lower()}.',
            'visual_direction': 'Editorial typography with one diagnostic annotation.',
            'sources': ['Ad Age'] if number < 8 else [],
            'source_support': 'Validated Evidence Pack' if number < 8 else 'Ad Doctor analysis',
            'quality_warnings': [] if number != 8 else ['Outcome evidence remains incomplete.'],
        }
        for number, slide_type in enumerate(types, 1)
    ]


def _package(case, score, gate, blockers=()):
    diagnosis = {
        'Symptoms': 'Nike launched an Olympics participation platform.',
        'Patient History': 'Sports sponsorship increasingly competes for active fan participation.',
        'Diagnosis': 'The underlying problem is attention that does not automatically become belonging.',
        'Treatment': 'Turn campaign exposure into a participatory community mechanic.',
        'Results': 'Long-term brand-memory results remain unconfirmed.',
        "Doctor’s Opinion": 'Strategically sound. Creatively familiar.',
        'Prescription': 'Build a distinctive participation ritual that survives the event window.',
    }
    return {
        'title': case.title,
        'hook': 'Is this a sponsorship campaign—or a treatment for passive attention?',
        'status': case.status,
        'quality': score,
        'sources': case.details['sources'],
        'diagnosis': diagnosis,
        'slides': _slides(),
        'facts': ['Nike launched the platform ahead of the Olympics.'],
        'claims': ['The brand says participation deepens community.'],
        'contradictions': ['No independent long-term lift is confirmed.'],
        'unknowns': ['Durable brand-memory impact remains uncertain.'],
        'source_catalog': [
            {'publication': 'Ad Age', 'article': 'Nike turns Olympic attention into participation', 'used_for': 'Launch facts'},
            {'publication': 'Campaign India', 'article': 'How Nike is building an Olympics community play', 'used_for': 'Participation mechanics'},
            {'publication': 'Marketing Week', 'article': 'Olympic sponsorship enters its community era', 'used_for': 'Category context'},
        ],
        'article': 'The campaign is best understood as a treatment for passive attention: it converts exposure into a repeatable act of participation while leaving long-term brand-memory impact unproven.',
        'quality_detail': {
            'gate': gate,
            'dimensions': {'Evidence': 94 if gate == 'Pass' else 61, 'Diagnosis': 92 if gate == 'Pass' else 72, 'Editorial clarity': 90 if gate == 'Pass' else 70, 'Traceability': 93 if gate == 'Pass' else 58},
            'blockers': list(blockers),
            'warnings': ['Long-term results remain unconfirmed.'],
            'traceability': '9 of 10 evidence-led slides map to validated facts or clearly marked Ad Doctor analysis.',
        },
        'findings': [{'severity': 'error' if blockers else 'warning', 'text': text} for text in (blockers or ('Long-term results remain unconfirmed.',))],
        'versions': ['Package v2 · Ready for review', 'Package v1 · Needs revision'],
        'quality_versions': [f'Quality review v2 · {score}/100 · {gate}', 'Quality review v1 · 74/100 · Needs revision'],
        'timeline': [{'label': f'Quality review {gate.lower()}', 'time': 'Today 10:14', 'detail': f'Score {score}'}, {'label': 'Draft created', 'time': 'Today 09:52', 'detail': 'Package v2'}],
        'approval_history': ['No approval recorded · Human approval required'],
    }


def demo_payload():
    cases = (
        CaseListItem('Why Nike is treating attention as a participation problem', 'Nike · Campaign Surgery', 'editorial_review_pending', '92 quality', {'brand': 'Nike', 'content_type': 'Campaign Surgery', 'quality': 92, 'sources': 4, 'approval': 'Pending', 'publication': 'Not published'}),
        CaseListItem('Coca-Cola’s retail media diagnosis', 'Coca-Cola · Brand Checkup', 'quality_review_failed', '68 quality', {'brand': 'Coca-Cola', 'content_type': 'Brand Checkup', 'quality': 68, 'sources': 3, 'approval': 'Needs revision', 'publication': 'Blocked'}),
    )
    sources = (
        SourceListItem('Campaign India', 'campaignindia.in', 'active', 'Healthy', {'type': 'Trade publication', 'last_scan': 'Today, 09:00', 'articles': 128, 'health': 'healthy'}),
        SourceListItem('Ad Age', 'adage.com', 'active', 'Healthy', {'type': 'Trade publication', 'last_scan': 'Today, 09:02', 'articles': 94, 'health': 'healthy'}),
        SourceListItem('Marketing Week', 'marketingweek.com', 'inactive', 'Needs attention', {'type': 'Trade publication', 'last_scan': 'Yesterday', 'articles': 51, 'health': 'warning'}),
    )
    sources = tuple(replace(source, details={**source.details,
        'id': index, 'active': source.status == 'active', 'category': 'Advertising',
        'country': 'Sample', 'credibility_tier': 2})
        for index, source in enumerate(sources, 1))
    articles = (
        ArticleListItem('Nike turns Olympic attention into participation', 'Ad Age · 18 Jul 2026', 'extracted', 'Linked', {'publication': 'Ad Age', 'date': '2026-07-18', 'summary': 'Nike’s move connects athlete attention with consumer participation.', 'topic': 'Nike Olympics participation campaign'}),
        ArticleListItem('How Nike is building an Olympics community play', 'Campaign India · 18 Jul 2026', 'extracted', 'Linked', {'publication': 'Campaign India', 'date': '2026-07-18', 'summary': 'The campaign uses community mechanics beyond conventional sponsorship.', 'topic': 'Nike Olympics participation campaign'}),
    )
    articles = tuple(replace(article, details={**article.details,
        'fetch_date': 'Sample run', 'extraction_status': 'Sample extraction',
        'article_text': 'Illustrative sample coverage for interface exploration. Not verified reporting.'})
        for article in articles)
    topics = (
        TopicQualificationItem('Nike Olympics participation campaign', 'Nike · 4 distinct publications', 'qualified', 'Qualified', {'count': 4, 'supporting': list(articles), 'excluded': ['Nike quarterly earnings recap'], 'reason': 'Covered by 4 distinct approved publications.'}),
        TopicQualificationItem('Retail media measurement debate', 'Category topic · 2 distinct publications', 'qualification_pending', 'Awaiting third publication', {'count': 2, 'supporting': list(articles[:1]), 'excluded': [], 'reason': 'One more distinct publication is required.'}),
        TopicQualificationItem('Unverified viral brand stunt', 'Unknown brand · 1 publication', 'rejected', 'Rejected', {'count': 1, 'supporting': [], 'excluded': ['Social repost without original reporting'], 'reason': 'Fewer than three distinct publications.'}),
    )
    passing = _package(cases[0], 92, 'Pass')
    failing = _package(cases[1], 68, 'Fail', ('Slide 6 makes a result claim not supported by the Evidence Pack.', 'Prescription is not specific enough to be actionable.'))
    failing['hook'] = 'Retail media promises precision—but is the measurement system clinically reliable?'
    studio = ContentStudioViewModel(tuple(cases), passing, {cases[0].title: passing, cases[1].title: failing})
    subscribers = (
        SubscriberListItem('p***@example.com', 'Priya · Strategy leaders', 'active', 'Confirmed', {'consent': 'confirmed', 'segment': 'Strategy leaders', 'frequency': 'Weekly'}),
        SubscriberListItem('a***@example.com', 'Arjun · General', 'paused', 'Confirmed', {'consent': 'confirmed', 'segment': 'General', 'frequency': 'Weekly'}),
        SubscriberListItem('m***@example.com', 'Maya · General', 'unsubscribed', 'Withdrawn', {'consent': 'withdrawn', 'segment': 'General', 'frequency': 'Monthly'}),
    )
    return {
        'dashboard': DashboardViewModel({'New articles': '24', 'Awaiting qualification': '3', 'Qualified cases': '8', 'Awaiting review': '4', 'Publishing ready': '2', 'Deliveries': '96%'}, (ListItem('Coca-Cola case needs revision', 'Quality review found two blocking issues', 'quality_review_failed', 'High'), ListItem('Marketing Week scan failed', 'The source could not be reached', 'failed', 'Medium')), cases, {'Sources': 'Healthy', 'Scheduler': 'Running', 'Research provider': 'Not connected', 'Content provider': 'Connected', 'Google Workspace': 'Not connected', 'Email provider': 'Configured'}, 'Today, 09:12', 'Tomorrow, 09:00'),
        'sources': sources, 'articles': articles, 'qualification': topics, 'cases': cases, 'content_studio': studio,
        'publishing': PublishingViewModel((cases[0],), (cases[1],), (ListItem('Nike participation diagnosis v1', 'Private Google Doc', 'published', 'Published'),), ('Google Workspace is not connected in demo mode.',)),
        'subscribers': subscribers,
        'automation': AutomationViewModel(True, 'Tomorrow, 09:00', 'Today, 09:12', 'Asia/Kolkata', ('09:00', '16:00'), (ListItem('Daily intelligence run', '24 articles · 8 cases', 'completed', '18 min'),)),
        'logs': (LogListItem('Quality review completed', 'Content quality · Case 104', 'success', 'Today 10:14'), LogListItem('Source response slowed', 'Article discovery · Run 220', 'warning', 'Today 09:08'), LogListItem('Source scan could not connect', 'Article discovery · Run 221', 'failed', 'Today 09:06')),
        'settings': (ProviderHealthViewModel('Browser Research', 'connected', 'Approved-source HTTP retrieval with Chromium fallback.', 'SQLite Sources'), ProviderHealthViewModel('Gemini Content', 'not_configured', 'Primary gemini-3.7-flash; 503-only fallback gemini-3.5-flash; search disabled.', 'Not configured'), ProviderHealthViewModel('Gemini Quality', 'not_configured', 'Primary gemini-3.7-flash; 503-only fallback gemini-3.5-flash; search disabled.', 'Not configured'), ProviderHealthViewModel('Perplexity', 'alternative', 'Historical compatibility only; not required for production research.', 'Advanced'), ProviderHealthViewModel('OpenAI (optional)', 'alternative', 'Optional historical provider; not required for production.', 'Advanced'), ProviderHealthViewModel('Google Workspace', 'not_configured', 'Google Workspace is optional.', 'Not configured'), ProviderHealthViewModel('Email', 'connected', 'Email delivery is configured.', 'Environment')),
    }
