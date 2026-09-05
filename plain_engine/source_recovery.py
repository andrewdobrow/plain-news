"""National source discovery and recovery, adapted from TCT's production pipeline."""
from __future__ import annotations
import html as html_lib, json, re, socket, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from urllib.parse import quote, urlsplit
try:
    import feedparser
except Exception:
    feedparser=None
import requests
from .generation_cache import CACHE_MISS, PersistentGenerationCache, cache_hash, normalize_cache_url, source_content_hint

FEED_TIMEOUT_SECONDS=9; SOURCE_FETCH_TIMEOUT_SECONDS=9; MIN_SOURCE_WORDS=80; FULL_SOURCE_WORDS=140
USER_AGENT='Mozilla/5.0 (compatible; PlainNewsBot/2.0; +https://plainnews.app)'
DISCOVERY_DOMAINS={'nytimes.com','washingtonpost.com','wsj.com','bloomberg.com','businessinsider.com','theinformation.com','ft.com'}
TRUSTED_PUBLISHER_DOMAINS={
 'Reuters':('reuters.com',),'AP':('apnews.com',),'BBC':('bbc.com','bbc.co.uk','bbci.co.uk'),'NPR':('npr.org',),'The Guardian':('theguardian.com',),
 'CNN':('cnn.com',),'NBC News':('nbcnews.com',),'CBS News':('cbsnews.com',),'ABC News':('abcnews.go.com',),'Fox News':('foxnews.com',),'USA Today':('usatoday.com',),
 'Politico':('politico.com',),'Axios':('axios.com',),'The Hill':('thehill.com',),'The Verge':('theverge.com',),'Ars Technica':('arstechnica.com',),'TechCrunch':('techcrunch.com',),
 'Variety':('variety.com',),'Rolling Stone':('rollingstone.com',),'ESPN':('espn.com',),'CBS Sports':('cbssports.com',)}
_LOCKS={}; _LOCK_GUARD=threading.Lock()

def get_domain(url):
    try:return urlsplit(str(url or '')).netloc.casefold().split(':',1)[0].removeprefix('www.')
    except Exception:return ''
def _domain_matches(domain,expected): return any(domain==d or domain.endswith('.'+d) for d in expected)
def sanitize_text(text): return re.sub(r'\s+',' ',re.sub(r'[\x00-\x1f\x7f-\x9f]',' ',re.sub(r'<[^>]+>',' ',html_lib.unescape(str(text or ''))))).strip()
def extract_rss_text(entry):
    values=[]
    content=[]
    try: content=entry.get('content',[]) or getattr(entry,'content',[]) or []
    except Exception: pass
    if isinstance(content,list):
        values += [str(r.get('value')) for r in content if isinstance(r,dict) and r.get('value')]
    for key in ('summary','description'):
        try: raw=entry.get(key,'') or getattr(entry,key,'')
        except Exception: raw=''
        if raw: values.append(str(raw))
    return sanitize_text(max(values,key=len) if values else '')
def extract_publisher_url(entry):
    try:return str(entry.get('link','') or getattr(entry,'link','') or '').strip()
    except Exception:return ''
def trusted_publisher_identity(entry,title=''):
    vals=[]
    try: source=entry.get('source',{})
    except Exception: source={}
    if isinstance(source,dict): vals.extend([str(source.get('title') or ''),str(source.get('href') or '')])
    elif source: vals.append(str(source))
    if ' - ' in str(title): vals.append(str(title).rsplit(' - ',1)[-1])
    blob=' '.join(vals).casefold()
    for pub,domains in TRUSTED_PUBLISHER_DOMAINS.items():
        if pub.casefold() in blob or any(d in blob for d in domains): return pub,domains
    return '',()
def classify_source(url):
    d=get_domain(url)
    if d=='news.google.com' or d.endswith('.news.google.com'):return 'aggregator'
    if _domain_matches(d,DISCOVERY_DOMAINS):return 'discovery_only'
    return 'full_source' if d else 'unknown'
def _empty_feed_document():
    if feedparser is None:
        from types import SimpleNamespace
        return SimpleNamespace(feed={},entries=[])
    return feedparser.FeedParserDict(feed=feedparser.FeedParserDict(),entries=[])
def fetch_feed_document(url):
    if feedparser is None:return _empty_feed_document()
    try:
        r=requests.get(url,timeout=FEED_TIMEOUT_SECONDS,headers={'User-Agent':USER_AGENT,'Accept':'application/rss+xml, application/xml, text/xml, */*'})
        return feedparser.parse(r.content) if r.status_code<400 else _empty_feed_document()
    except Exception:
        try:
            old=socket.getdefaulttimeout(); socket.setdefaulttimeout(FEED_TIMEOUT_SECONDS); parsed=feedparser.parse(url); socket.setdefaulttimeout(old); return parsed
        except Exception:return _empty_feed_document()
def prefetch_feed_documents(urls):
    ordered=sorted({str(u).strip() for u in urls if str(u).strip()}); docs={u:_empty_feed_document() for u in ordered}
    with ThreadPoolExecutor(max_workers=min(12,max(1,len(ordered)))) as ex:
        futs={ex.submit(fetch_feed_document,u):u for u in ordered}
        for f in as_completed(futs):
            try:docs[futs[f]]=f.result()
            except Exception:pass
    return docs
def _lock(key):
    with _LOCK_GUARD:return _LOCKS.setdefault(key,threading.Lock())
def _clean_article_text(raw,max_words):
    text=re.sub(r'<script.*?</script>|<style.*?</style>',' ',str(raw or ''),flags=re.S|re.I); text=sanitize_text(text)
    junk=('subscribe','sign up','cookie','advertisement','all rights reserved','privacy policy','newsletter','download our app','manage preferences')
    sentences=re.split(r'(?<=[.!?])\s+',text); kept=[s.strip() for s in sentences if len(s.split())>=5 and not any(j in s.casefold() for j in junk)]
    return ' '.join(' '.join(kept).split()[:max_words])
def focus_extracted_source_text(text,source):
    text=re.sub(r'\s+',' ',str(text or '')).strip(); source=source or {}
    if len(text.split())<220:return text
    title=str(source.get('title') or source.get('source_title') or '')
    generic={'after','before','following','from','into','with','without','state','location','news','says','said','report','reports','update','latest'}
    tokens={t for t in re.findall(r'[a-z0-9]+',title.casefold()) if len(t)>=4 and t not in generic}
    if len(tokens)<2:return text
    sentences=[x.strip() for x in re.split(r'(?<=[.!?])\s+',text) if x.strip()]; scores=[len(set(re.findall(r'[a-z0-9]+',s.casefold()))&tokens) for s in sentences]
    if not scores or max(scores)<2:return text
    anchor=max(range(len(scores)),key=scores.__getitem__); prefix=sum(len(s.split()) for s in sentences[:anchor])
    if prefix<=140:return text
    start=max(0,anchor-1); end=min(len(sentences),anchor+5); focused=' '.join(sentences[start:end])
    return focused if len(focused.split())>=45 and len(focused.split())<len(text.split())*.85 else text
def fetch_article_text(url,*,cache,max_words=2500,content_hint=''):
    if not url:return ''
    key=cache_hash({'v':'plain-source-v3','url':normalize_cache_url(url),'max_words':max_words,'hint':content_hint})
    cached=cache.get('source_text',key)
    if cached is not CACHE_MISS:return str((cached or {}).get('text',''))
    with _lock(key):
        cached=cache.get('source_text',key)
        if cached is not CACHE_MISS:return str((cached or {}).get('text',''))
        text=''
        try:
            r=requests.get(url,timeout=SOURCE_FETCH_TIMEOUT_SECONDS,allow_redirects=True,headers={'User-Agent':USER_AGENT,'Accept-Language':'en-US,en;q=0.9'})
            if r.status_code==200:
                page=r.text
                try:
                    import trafilatura
                    cand=_clean_article_text(trafilatura.extract(page,url=url,include_comments=False,include_tables=False,favor_precision=False) or '',max_words)
                    if len(cand.split())>=FULL_SOURCE_WORDS:text=cand
                except Exception:pass
                if not text:
                    for raw in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',page,flags=re.S|re.I):
                        try:data=json.loads(raw.strip())
                        except Exception:continue
                        stack=data if isinstance(data,list) else [data]; nodes=[]
                        for item in stack:
                            if isinstance(item,dict) and isinstance(item.get('@graph'),list):nodes.extend(item['@graph'])
                            else:nodes.append(item)
                        for node in nodes:
                            if isinstance(node,dict) and node.get('articleBody'):
                                cand=_clean_article_text(node['articleBody'],max_words)
                                if len(cand.split())>=FULL_SOURCE_WORDS:text=cand;break
                        if text:break
                if not text:
                    art=re.search(r'<article[^>]*>(.*?)</article>',page,flags=re.S|re.I); scope=art.group(1) if art else page
                    cand=_clean_article_text(' '.join(re.findall(r'<p[^>]*>(.*?)</p>',scope,flags=re.S|re.I)),max_words)
                    if len(cand.split())>=MIN_SOURCE_WORDS:text=cand
        except Exception:pass
        cache.put('source_text',key,{'text':text,'word_count':len(text.split()),'url':normalize_cache_url(url)},ttl_seconds=86400 if text else 7200);return text
def _google_token(url):
    m=re.search(r'/(?:rss/)?articles/([^/?#]+)',urlsplit(str(url)).path);return m.group(1) if m else ''
def _extract_rpc_url(text):
    raw=str(text or '')
    # Robust enough for the garturlres response shape used by Google News.
    m=re.search(r'garturlres[^h]+(https?[^"\\]+)',raw)
    return html_lib.unescape(m.group(1).replace(r'\/','/').replace(r'\u0026','&')) if m else ''
def resolve_google_news_url(url,*,cache,expected_domains=()):
    raw=str(url or '').strip()
    if 'news.google.com' not in raw:return raw
    key=cache_hash({'v':'google-news-resolve-v2','url':normalize_cache_url(raw)}); cached=cache.get('source_resolutions',key)
    if cached is not CACHE_MISS:return str((cached or {}).get('resolved_url',''))
    token=_google_token(raw); resolved=''
    try:
        headers={'User-Agent':USER_AGENT,'Accept-Language':'en-US,en;q=0.9'}; landing=requests.get(f'https://news.google.com/rss/articles/{token}',timeout=6,headers=headers,allow_redirects=True)
        direct=str(getattr(landing,'url','') or '')
        if direct and 'news.google.com' not in direct:resolved=direct
        else:
            body=str(getattr(landing,'text','') or ''); sig=re.search(r'data-n-a-sg=["\']([^"\']+)',body); ts=re.search(r'data-n-a-ts=["\']([^"\']+)',body)
            if sig and ts:
                inner=f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{token}",{ts.group(1)},"{sig.group(1)}"]'
                payload=['Fbv4je',inner]; rpc=requests.post('https://news.google.com/_/DotsSplashUi/data/batchexecute',timeout=6,headers={**headers,'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'},data=f'f.req={quote(json.dumps([[payload]]))}'); resolved=_extract_rpc_url(rpc.text)
        domain=get_domain(resolved); expected=tuple(expected_domains)
        if not resolved or 'news.google.com' in domain or (expected and not _domain_matches(domain,expected)):resolved=''
    except Exception:resolved=''
    cache.put('source_resolutions',key,{'resolved_url':resolved},ttl_seconds=7*86400 if resolved else 7200);return resolved
def _pub_ts(v):
    try:return parsedate_to_datetime(str(v)).astimezone(timezone.utc).timestamp()
    except Exception:
        try:
            from datetime import datetime
            return datetime.fromisoformat(str(v).replace('Z','+00:00')).timestamp()
        except Exception:return 0
def fetch_headlines(feeds,*,cache,limit=18,feed_documents=None,image_extractor=None):
    docs=feed_documents or {}; seen_titles=set(); seen_urls=set(); rows=[]
    for feed_url in feeds:
        feed=docs.get(feed_url) or fetch_feed_document(feed_url)
        for entry in list(getattr(feed,'entries',[]) or [])[:35]:
            title=sanitize_text(entry.get('title',''))
            if not title:continue
            summary=extract_rss_text(entry)[:12000]; raw=extract_publisher_url(entry); norm=normalize_cache_url(raw); tk=re.sub(r'[^a-z0-9]+',' ',title.casefold()).strip()
            if tk in seen_titles or (norm and norm in seen_urls):continue
            seen_titles.add(tk); seen_urls.add(norm) if norm else None
            pub,domains=trusted_publisher_identity(entry,title); image=''
            try:image=str(image_extractor(entry) or '') if image_extractor else ''
            except Exception:pass
            rows.append({'title':title,'summary':summary,'link':raw,'source_url':raw,'aggregator_url':raw if 'news.google.com' in raw else '','publisher_name':pub,'publisher_domains':list(domains),'feed_url':feed_url,'source_type':classify_source(raw),'source_quality':'unclassified','image_url':image,'published':entry.get('published','') or entry.get('updated','')})
    rows.sort(key=lambda r:_pub_ts(r.get('published')),reverse=True); result=rows[:max(1,limit)]
    def enrich(row):
        link=row.get('link',''); sw=len(str(row.get('summary') or '').split())
        if row.get('source_type')=='aggregator':
            resolved=resolve_google_news_url(row.get('aggregator_url') or link,cache=cache,expected_domains=tuple(row.get('publisher_domains') or ()))
            if resolved:row['link']=row['source_url']=link=resolved;row['source_type']=classify_source(resolved);row['source_recovery_status']='resolved_google_news'
            else:row['source_quality']='brief' if sw>=40 else 'thin';return row
        if row.get('source_type')!='discovery_only' and link:
            full=fetch_article_text(link,cache=cache,max_words=2500,content_hint=source_content_hint(row)); focused=focus_extracted_source_text(full,row)
            if focused!=full:row['source_focus_repaired']=True
            if len(focused.split())>=FULL_SOURCE_WORDS:row['article_text']=focused;row['source_quality']='full';return row
        if sw>=FULL_SOURCE_WORDS:row['article_text']=row.get('summary','');row['source_quality']='full'
        elif sw>=MIN_SOURCE_WORDS:row['article_text']=row.get('summary','');row['source_quality']='summary'
        elif sw>=40:row['source_quality']='brief'
        elif row.get('source_type')=='discovery_only':row['source_quality']='discovery_only'
        else:row['source_quality']='thin'
        return row
    with ThreadPoolExecutor(max_workers=min(8,max(1,len(result)))) as ex:
        futs=[ex.submit(enrich,r) for r in result]
        for f in as_completed(futs):
            try:f.result()
            except Exception:pass
    return result
def build_content_bank(feed_documents,feed_urls):
    bank=[]
    for url in feed_urls:
        feed=feed_documents.get(url) or fetch_feed_document(url)
        for e in list(getattr(feed,'entries',[]) or [])[:60]:
            title=sanitize_text(e.get('title','')); summary=extract_rss_text(e)
            if title and summary:bank.append({'title':title,'summary':summary,'link':extract_publisher_url(e),'feed_url':url})
    return bank
def fetch_guardian_article(headline,*,api_key,cache):
    m=fetch_guardian_match(headline,api_key=api_key,cache=cache);return str((m or {}).get('article_text',''))
def fetch_guardian_match(headline,*,api_key,cache):
    if not api_key or not headline:return None
    key=cache_hash({'guardian_match':'v1','headline':headline.casefold().strip()}); cached=cache.get('guardian_text',key)
    if cached is not CACHE_MISS:
        v=(cached or {}).get('match');return dict(v) if isinstance(v,dict) else None
    match=None
    try:
        r=requests.get('https://content.guardianapis.com/search',params={'q':headline,'api-key':api_key,'show-fields':'bodyText,headline,thumbnail','page-size':3,'order-by':'newest'},timeout=10,headers={'User-Agent':USER_AGENT}); payload=r.json() if r.status_code==200 else {}; candidates=((payload.get('response') or {}).get('results') or [])
        ht={t for t in re.findall(r'[a-z0-9]+',headline.casefold()) if len(t)>=4}; scored=[]
        for c in candidates:
            f=c.get('fields') or {}; ch=str(f.get('headline') or c.get('webTitle') or ''); ct={t for t in re.findall(r'[a-z0-9]+',ch.casefold()) if len(t)>=4}; score=len(ht&ct)/max(1,min(len(ht),len(ct))); body=sanitize_text(f.get('bodyText') or '')
            if score>=.45 and len(body.split())>=MIN_SOURCE_WORDS:scored.append((score,c,f,body,ch))
        if scored:
            _,c,f,body,ch=max(scored,key=lambda x:x[0]); match={'title':ch,'summary':body[:1800],'article_text':body,'link':str(c.get('webUrl') or ''),'source_url':str(c.get('webUrl') or ''),'publisher_name':'The Guardian','publisher_domains':['theguardian.com'],'source_type':'full_source','source_quality':'full','image_url':str(f.get('thumbnail') or ''),'published':str(c.get('webPublicationDate') or ''),'source_recovery_status':'guardian_alternate_exact_source'}
    except Exception:pass
    cache.put('guardian_text',key,{'match':match},ttl_seconds=86400 if match else 3600);return match
