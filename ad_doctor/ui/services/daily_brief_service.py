"""Read-only daily collection view, using the team's India calendar day."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def daily_brief(service, now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo('Asia/Kolkata'))
    result = {'date': local.strftime('%d %b %Y'), 'timezone': 'Asia/Kolkata',
              'count': 0, 'articles': [], 'sample': service.demo}
    if service.demo:
        result['articles'] = [dict(title=x.title, publication=x.details['publication'],
                                   summary=x.details['summary'], url=None)
                              for x in service.get('articles')]
        result['count'] = len(result['articles'])
        return result
    repository = service._production.repository
    if not repository.has_table('articles'):
        return result
    start = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    bounds = (start.astimezone(timezone.utc).isoformat(),
              (start + timedelta(days=1)).astimezone(timezone.utc).isoformat())
    where = 'julianday(fetch_timestamp)>=julianday(?) AND julianday(fetch_timestamp)<julianday(?)'
    result['count'] = repository.scalar(f'SELECT COUNT(*) FROM articles WHERE {where}', bounds)
    rows = repository.rows(f'SELECT headline,source_name,meta_description,article_url FROM articles WHERE {where} ORDER BY fetch_timestamp DESC,id DESC LIMIT 20', bounds)
    result['articles'] = [dict(title=r['headline'], publication=r['source_name'],
                               summary=r['meta_description'] or '', url=r['article_url']) for r in rows]
    return result
