"""Batch national category-fit classifier with persistent cache."""
from __future__ import annotations
import json,re
from .generation_cache import CACHE_MISS,cache_hash
from .model_response import extract_model_text
MODEL='claude-sonnet-4-5';VALID={'world','us','politics','business','tech','sports','entertainment','none'}
def _parse(t):
 raw=re.sub(r'^```(?:json)?\s*|\s*```$','',str(t or '').strip(),flags=re.I)
 try:
  from json_repair import repair_json
  return json.loads(repair_json(raw))
 except Exception:return json.loads(raw,strict=False)
def classify_stories(client,rows,cache):
 if not rows:return {}
 key=cache_hash({'v':'plain-category-v2','rows':[(r.get('title',''),r.get('link',''),r.get('published','')) for r in rows]});c=cache.get('classifications',key)
 if c is not CACHE_MISS:return {int(k):v for k,v in (c or {}).get('mapping',{}).items()}
 packet=[{'index':i,'title':r.get('title',''),'summary':str(r.get('summary',''))[:900],'publisher':r.get('publisher_name','')} for i,r in enumerate(rows,1)]
 prompt=f'''Classify each source story for Plain, a nationwide U.S. general-news publication. Return ONLY a JSON object mapping source index strings to arrays of category keys. Allowed: world, us, politics, business, tech, sports, entertainment, none. Multiple categories only when genuinely central. Use none for routine local incidents without national significance, promotional/listing content, opinion-only items, evergreen service material, or non-news. world=consequential non-U.S. events/diplomacy/war; us=major U.S. national/state developments; politics=U.S. government/elections/policy; business=economy/markets/companies/labor/trade; tech=technology/science/space; sports=meaningful sports news; entertainment=film/TV/music/media/culture. SOURCES:{json.dumps(packet,ensure_ascii=False)}'''
 try:
  response=client.messages.create(model=MODEL,max_tokens=1800,messages=[{'role':'user','content':prompt}]);d=_parse(extract_model_text(response));m={}
  for i in range(1,len(rows)+1):
   raw=d.get(str(i),[]) if isinstance(d,dict) else [];raw=[raw] if isinstance(raw,str) else raw;vals=[str(x).strip().lower() for x in raw if str(x).strip().lower() in VALID];m[i]=vals or ['none']
 except Exception:m={i:[] for i in range(1,len(rows)+1)}
 cache.put('classifications',key,{'mapping':{str(k):v for k,v in m.items()}},ttl_seconds=12*3600);return m
