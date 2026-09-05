"""TCT-derived source-image authority for Plain."""
from __future__ import annotations
import hashlib,json,re
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urljoin,urlsplit
import requests
USER_AGENT='Mozilla/5.0 (compatible; PlainNewsBot/2.0; +https://plainnews.app)'
PLACEHOLDER_TOKENS=('1x1','pixel','spacer','tracking','transparent','placeholder','default-image','default_image','news-slate','site-logo','station-logo','publisher-logo','masthead-logo','brand-logo','brandmark','favicon','apple-touch-icon','logo-square','logo-horizontal','social-logo','generic-share','og-image.png','top_image','brand-icons','avatar-default','noimage','no-image','missing-image')
CATEGORY_WORDS={'world':{'world','war','military','foreign','international','gaza','ukraine','russia','china','iran','israel','europe'},'us':{'america','american','u.s','us','state','federal','nation','court','police','weather','storm'},'politics':{'trump','congress','senate','house','white','president','election','campaign','federal','administration'},'business':{'business','market','stock','company','economy','bank','fed','jobs','trade','tariff','earnings'},'tech':{'technology','tech','science','ai','software','chip','space','nasa','apple','google','microsoft','meta'},'sports':{'sports','game','team','league','nba','nfl','mlb','nhl','soccer','football','baseball','basketball','tennis'},'entertainment':{'movie','film','music','tv','television','actor','actress','singer','celebrity','hollywood','netflix'}}
def _domain(url):
 try:return urlsplit(str(url or '')).netloc.casefold().removeprefix('www.')
 except Exception:return ''
def image_rejection_reason(img_url,*,source_url='',alt_text='',width=None,height=None):
 url=str(img_url or '').strip(); low=url.casefold()
 if not url or len(url)<15:return 'missing_or_short'
 if low.startswith('data:'):return 'data_url'
 if any(x in low for x in PLACEHOLDER_TOKENS):return 'placeholder_or_logo_url'
 alt=str(alt_text or '').casefold()
 if alt and any(x in alt for x in ('logo','icon','brand','masthead','publisher','station')) and len(alt.split())<=8:return 'logo_alt_text'
 try:w=int(float(width)) if width not in (None,'') else None;h=int(float(height)) if height not in (None,'') else None
 except Exception:w=h=None
 if w and h and min(w,h)<=80:return 'tiny_dimensions'
 if w and h and max(w/h,h/w)>=6:return 'extreme_aspect_ratio'
 if _domain(url)==_domain(source_url) and re.search(r'/(?:logo|logos|brand|branding|assets/logo)(?:[._/-]|$)',urlsplit(url).path.casefold()):return 'publisher_brand_asset'
 return ''
def valid_source_image(url,**kwargs):return not image_rejection_reason(url,**kwargs)
def extract_image(entry):
 source=str(entry.get('link','') if hasattr(entry,'get') else ''); candidates=[]
 for key in ('media_content','media_thumbnail'):
  rows=getattr(entry,key,None) or (entry.get(key,[]) if hasattr(entry,'get') else []) or []
  for row in rows:
   if isinstance(row,dict):candidates.append((str(row.get('url') or ''),row.get('width'),row.get('height'),str(row.get('title') or row.get('description') or '')))
 if hasattr(entry,'get'):
  for row in entry.get('enclosures',[]) or []:
   if isinstance(row,dict):candidates.append((str(row.get('href') or row.get('url') or ''),row.get('width'),row.get('height'),''))
 for u,w,h,a in candidates:
  if valid_source_image(u,source_url=source,alt_text=a,width=w,height=h):return u
 return ''
def fetch_og_image(url,*,headline='',timeout=8):
 if not url:return ''
 try:
  r=requests.get(url,timeout=timeout,allow_redirects=True,headers={'User-Agent':USER_AGENT,'Accept-Language':'en-US,en;q=0.9'}); page=r.text[:1500000] if r.status_code==200 else ''
  pats=[r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image(?::secure_url)?["\']',r'<meta[^>]+(?:property|name)=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)']
  for pat in pats:
   for m in re.findall(pat,page,flags=re.I):
    c=urljoin(str(getattr(r,'url',url) or url),m.replace('&amp;','&'))
    if valid_source_image(c,source_url=url,alt_text=headline):return c
 except Exception:pass
 return ''
def build_image_bank(feed_documents,feed_urls):
 bank=[];seen=set()
 for feed_url in feed_urls:
  feed=feed_documents.get(feed_url)
  if not feed:continue
  for e in list(getattr(feed,'entries',[]) or [])[:70]:
   img=extract_image(e)
   if not img or img in seen:continue
   seen.add(img); title=str(e.get('title','') if hasattr(e,'get') else '');link=str(e.get('link','') if hasattr(e,'get') else '')
   bank.append({'title':title,'image_url':img,'link':link,'source_domain':_domain(link),'feed_url':feed_url})
 return bank
def _tokens(text):
 stops={'the','a','an','in','on','at','to','for','of','and','or','is','are','was','were','with','its','by','as','from','that','this','after','over','into','about','amid','during','says','say','new','latest','update'}
 return {w for w in re.findall(r'[a-z0-9]+',str(text or '').casefold()) if len(w)>=3 and w not in stops}
def match_image(headline,image_bank,category_key='',*,used_images=None):
 target=_tokens(headline);used_images=used_images or set();best=(0,None);cat=CATEGORY_WORDS.get(category_key,set())
 for row in image_bank:
  img=str(row.get('image_url') or '');rt=_tokens(row.get('title',''))
  if not img or img in used_images or not valid_source_image(img,source_url=row.get('link','')):continue
  ov=len(target&rt)
  if ov<2:continue
  score=ov*3+(ov/max(1,min(len(target),len(rt))))*4+(.75 if cat and rt&cat else 0)
  if score>best[0]:best=(score,row)
 if not best[1]:return '',''
 return str(best[1].get('image_url') or ''),str(best[1].get('source_domain') or '')
def restore_source_image_from_archive(item,archive):
 sid=str(item.get('story_id') or '');source=str(item.get('link') or '');title=re.sub(r'[^a-z0-9]+',' ',str(item.get('source_title') or '').casefold()).strip()
 for row in reversed(archive):
  match=bool(sid and row.get('story_id')==sid) or bool(source and row.get('source_url')==source)
  if not match and title:match=re.sub(r'[^a-z0-9]+',' ',str(row.get('source_headline') or row.get('headline') or '').casefold()).strip()==title
  if match:
   img=str(row.get('source_image_url') or row.get('image_url') or '')
   if valid_source_image(img,source_url=row.get('source_url','')):item['image_url']=img;item['image_credit']=row.get('image_credit','');item['image_origin']='archive_source_authority';return True
 return False
class FallbackRotator:
 def __init__(self,root,site_url,state_path=None):
  self.root=Path(root);self.site_url=site_url.rstrip('/');self.state_path=state_path or self.root/'data'/'editorial-image-rotation.json';self.state_path.parent.mkdir(parents=True,exist_ok=True)
  try:self.state=json.loads(self.state_path.read_text())
  except Exception:self.state={'categories':{}}
 def _available(self,cat):
  out=[]
  for i in range(1,8):
   for ext in ('jpg','jpeg','png','webp'):
    p=self.root/'images'/'fallback'/f'{cat}-{i}.{ext}'
    if p.exists():out.append(p.name);break
  return out or (self._available('top_news') if cat!='top_news' else [])
 def choose(self,cat,headline=''):
  a=self._available(cat)
  if not a:return '',''
  st=self.state.setdefault('categories',{}).setdefault(cat,{'counter':0,'last':''});idx=(int(st.get('counter',0))+int(hashlib.sha256(str(headline or cat).encode()).hexdigest(),16))%len(a)
  if len(a)>1 and a[idx]==st.get('last'):idx=(idx+1)%len(a)
  st['counter']=(int(st.get('counter',0))+1)%len(a);st['last']=a[idx];return f'{self.site_url}/images/fallback/{a[idx]}','Plain'
 def save(self):
  self.state['updated_at']=datetime.now(timezone.utc).isoformat();tmp=self.state_path.with_suffix('.json.tmp');tmp.write_text(json.dumps(self.state,indent=2));tmp.replace(self.state_path)
def ensure_item_image(item,*,category_key,image_bank,archive,rotator,used_images=None):
 used_images=used_images or set();cur=str(item.get('image_url') or '')
 if cur and not valid_source_image(cur,source_url=item.get('link','')):item['image_rejection_reason']=image_rejection_reason(cur,source_url=item.get('link',''));item['image_url']='';cur=''
 if cur:item.setdefault('image_origin','rss_source');used_images.add(cur);return item
 if restore_source_image_from_archive(item,archive):used_images.add(item['image_url']);return item
 og=fetch_og_image(str(item.get('link') or ''),headline=str(item.get('source_title') or item.get('headline') or ''))
 if og:item['image_url']=og;item['image_credit']=_domain(item.get('link',''));item['image_origin']='source_social_image';used_images.add(og);return item
 m,c=match_image(str(item.get('source_title') or item.get('headline') or ''),image_bank,category_key,used_images=used_images)
 if m:item['image_url']=m;item['image_credit']=c;item['image_origin']='rss_related_source';used_images.add(m);return item
 f,c=rotator.choose(category_key,str(item.get('headline') or ''));item['image_url']=f;item['image_credit']=c;item['image_origin']='editorial_fallback';item['is_fallback_image']=True
 if f:used_images.add(f)
 return item
def write_image_quality_report(categories,path):
 rows=[];counts={}
 for cat in categories:
  for slot,item in [('hero',cat.get('hero',{}))]+[('card',x) for x in cat.get('cards',[])]:
   if not isinstance(item,dict):continue
   origin=str(item.get('image_origin') or 'unknown');counts[origin]=counts.get(origin,0)+1;rows.append({'category':cat.get('category_key'),'slot':slot,'headline':item.get('headline'),'image_url':item.get('image_url'),'origin':origin,'rejection':item.get('image_rejection_reason','')})
 report={'generated_at':datetime.now(timezone.utc).isoformat(),'counts':counts,'items':rows};Path(path).write_text(json.dumps(report,ensure_ascii=False,indent=2));return report
