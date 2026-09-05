"""Sonnet 5 assignment editor -> exact-source Sonnet 4.5 writer."""
from __future__ import annotations
import json,re,time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from .article_quality import publication_quality
from .editorial_rules import SYSTEM_PROMPT,category_rule
from .model_response import extract_model_text
ASSIGNMENT_EDITOR_MODEL='claude-sonnet-5';WRITER_MODEL='claude-sonnet-4-5';DEFAULT_CARD_COUNT=8
# Sonnet 5 enables adaptive thinking by default. This bounded assignment task only
# needs compact structured selection JSON, so disable thinking explicitly rather
# than letting reasoning consume the 1,800-token response ceiling before JSON.
ASSIGNMENT_EDITOR_THINKING={'type':'disabled'}

def _parse(text):
 raw=str(text or '').strip();raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw,flags=re.I)
 try:
  from json_repair import repair_json
  return json.loads(repair_json(raw))
 except Exception:
  a,b=raw.find('{'),raw.rfind('}');return json.loads(raw[a:b+1],strict=False)
def _age(raw):
 try:return max(0,(datetime.now(timezone.utc)-parsedate_to_datetime(str(raw)).astimezone(timezone.utc)).total_seconds()/3600)
 except Exception:return None
def _record(i,s):
 q=str(s.get('source_quality') or 'thin');words=len(str(s.get('article_text') or s.get('summary') or '').split());age=_age(s.get('published'))
 return {
  'source_index':i,'title':s.get('title',''),'published':s.get('published',''),'publisher':s.get('publisher_name',''),
  'source_quality':q,'source_word_count':words,'hero_eligible':q in {'full','summary'} and words>=80 and (age is None or age<=72),
  'summary':str(s.get('summary') or '')[:1500],'article_text':str(s.get('article_text') or '')[:11000],
  'canonical_context':s.get('canonical_context') or {},
  'material_update_candidate':bool(s.get('pre_generation_material_update')),
  'material_update_canonical_slug':str(s.get('pre_generation_material_update_canonical_slug') or ''),
  'material_update_confidence':float(s.get('material_update_confidence') or 0.0),
  'material_update_novel_facts':list(s.get('material_update_novel_facts') or []),
 }
def build_assignment_editor_packet(category_key,category_label,sources):return json.dumps({'publication':'Plain','audience':'nationwide U.S. general-news audience','category_key':category_key,'category_label':category_label,'category_rule':category_rule(category_key),'sources':[_record(i,s) for i,s in enumerate(sources,1)]},ensure_ascii=False)
def run_assignment_editor(client,category_key,category_label,sources,*,card_count=DEFAULT_CARD_COUNT,timeout_seconds=60):
 prompt=f'''You are Plain's assignment editor. SELECT exact source indices and angles; DO NOT write article copy.
Plain is a nationwide U.S. general-news product. Category rule: {category_rule(category_key)}
Reject routine local incidents without national significance, promotions/listicles, opinion-only items, thin/discovery-only sources, stale reprints and category misfits. Choose one hero only from hero_eligible=true and up to {card_count} distinct cards. A source with material_update_candidate=true has already passed a bounded semantic same-event/material-new-facts gate against an existing Plain canonical; treat it as a protected current development and strongly prefer selecting it unless it is unsafe or outside this category. Do not assign two reports of the same event.
Return ONLY JSON: {{"hero":{{"source_index":1,"angle":"specific factual angle","urgency_score":8}},"cards":[{{"source_index":2,"angle":"...","urgency_score":6}}],"rejected":[{{"source_index":3,"reason":"..."}}]}}
SOURCE PACKET:\n{build_assignment_editor_packet(category_key,category_label,sources)}'''
 r=client.with_options(timeout=max(1,float(timeout_seconds)),max_retries=0).messages.create(model=ASSIGNMENT_EDITOR_MODEL,max_tokens=1800,thinking=ASSIGNMENT_EDITOR_THINKING,system=[{'type':'text','text':SYSTEM_PROMPT,'cache_control':{'type':'ephemeral'}}],messages=[{'role':'user','content':prompt}],extra_headers={'anthropic-beta':'prompt-caching-2024-07-31'});data=_parse(extract_model_text(r));valid=set(range(1,len(sources)+1));hero=data.get('hero') if isinstance(data,dict) and isinstance(data.get('hero'),dict) else None
 try:hi=int(hero.get('source_index')) if hero else 0
 except Exception:hi=0
 if hi not in valid or not _record(hi,sources[hi-1])['hero_eligible']:hero=None
 cards=[];used={hi} if hero else set()
 for row in data.get('cards',[]) if isinstance(data,dict) and isinstance(data.get('cards'),list) else []:
  try:i=int(row.get('source_index'))
  except Exception:continue
  if i in valid and i not in used:used.add(i);cards.append(row)
  if len(cards)>=card_count:break
 if not hero:
  for i,s in enumerate(sources,1):
   if _record(i,s)['hero_eligible']:hero={'source_index':i,'angle':s.get('title',''),'urgency_score':5,'fallback':True};cards=[c for c in cards if int(c.get('source_index',0))!=i];break
 if not hero:raise ValueError('no hero-eligible exact source')
 return {'hero':hero,'cards':cards,'rejected':data.get('rejected',[]) if isinstance(data,dict) else []}
def _context(s):
 c=s.get('canonical_context') or {}
 return json.dumps({'existing_headline':c.get('headline',''),'existing_body':str(c.get('body',''))[:5000],'existing_slug':c.get('slug',''),'instruction':'For a material update, establish the original event and then what changed.'},ensure_ascii=False) if c else ''
def run_assignment_writer(client,*,category_key,category_label,source,assignment,hero,timeout_seconds=90):
 text=str(source.get('article_text') or source.get('summary') or '').strip()
 if len(text.split())<40:raise ValueError('assigned source too thin')
 target='330-430 words in 4-5 paragraphs' if hero else '100-150 words in 2 short paragraphs';schema='{"headline":"...","body":"..."}' if hero else '{"headline":"...","teaser":"...","body":"..."}'
 prompt=f'''Write the assigned Plain story. You are a WRITER, not an assignment editor. Use ONLY the exact source below plus prior canonical context. Never blend another story or source.
Category: {category_label}. Rule: {category_rule(category_key)}. Assigned angle: {assignment.get('angle','')}. Target: {target}.
ARTICLE CONTRACT: The lead must stand alone and establish who/what/where plus the principal new fact. Any money, percentage, named policy/program, jurisdiction, or major quantitative claim in the headline must also be in the lead. Use full names on first reference. For an update, establish both the original event and what is newly confirmed. Do not invent context, consequences, reactions, motives or what happens next. Do not pad missing information. Paraphrase the source. Return ONLY JSON: {schema}
EXACT SOURCE TITLE: {source.get('title','')}\nSOURCE PUBLISHED: {source.get('published','')}\nSOURCE QUALITY: {source.get('source_quality','')}\nSOURCE TEXT:\n{text[:12000]}\nPRIOR CANONICAL CONTEXT:\n{_context(source)}'''
 r=client.with_options(timeout=max(1,float(timeout_seconds)),max_retries=0).messages.create(model=WRITER_MODEL,max_tokens=2600 if hero else 1200,system=[{'type':'text','text':SYSTEM_PROMPT,'cache_control':{'type':'ephemeral'}}],messages=[{'role':'user','content':prompt}],extra_headers={'anthropic-beta':'prompt-caching-2024-07-31'});d=_parse(extract_model_text(r))
 item={'headline':str(d.get('headline') or '').strip(),'body':str(d.get('body') or '').strip(),'teaser':str(d.get('teaser') or '').strip(),'urgency_score':int(assignment.get('urgency_score') or 5),'source_index':int(assignment.get('source_index')),'published':str(source.get('published') or ''),'source_published_raw':str(source.get('published') or ''),'link':str(source.get('link') or source.get('source_url') or ''),'source_title':str(source.get('title') or ''),'source_summary':str(source.get('summary') or ''),'article_text':text,'source_quality':str(source.get('source_quality') or ''),'source_name':str(source.get('publisher_name') or ''),'image_url':str(source.get('image_url') or ''),'assignment_angle':str(assignment.get('angle') or '')}
 if source.get('canonical_context'):item['canonical_context']=source['canonical_context']
 if source.get('pre_generation_material_update'):
  item['pre_generation_material_update']=True
  item['pre_generation_material_update_canonical_slug']=str(source.get('pre_generation_material_update_canonical_slug') or '')
  item['semantic_material_update_decision']=source.get('semantic_material_update_decision') or {}
  item['material_update_novel_facts']=list(source.get('material_update_novel_facts') or [])
  item['material_update_confidence']=float(source.get('material_update_confidence') or 0.0)
 ok,reasons=publication_quality(item,hero=hero);item['publication_quality_ok']=ok;item['publication_quality_reasons']=reasons;return item
def run_live_assignment_category(client,category_key,category_label,sources,*,card_count=DEFAULT_CARD_COUNT,timeout_seconds=180):
 started=time.perf_counter();deadline=started+max(1,float(timeout_seconds))
 def remaining(default):
  left=deadline-time.perf_counter()
  if left<=1:raise TimeoutError(f'category budget exhausted for {category_label}')
  return min(default,left)
 plan=run_assignment_editor(client,category_key,category_label,sources,card_count=card_count,timeout_seconds=remaining(60));ha=plan['hero'];hero_source=sources[int(ha['source_index'])-1];hero=run_assignment_writer(client,category_key=category_key,category_label=category_label,source=hero_source,assignment=ha,hero=True,timeout_seconds=remaining(90))
 if hero_source.get('pre_generation_material_update') and not hero.get('publication_quality_ok'):
  raise RuntimeError('selected validated material update hero failed publication-quality contract')
 cards=[];fail=[]
 for a in plan.get('cards',[]):
  source=sources[int(a['source_index'])-1]
  try:
   c=run_assignment_writer(client,category_key=category_key,category_label=category_label,source=source,assignment=a,hero=False,timeout_seconds=remaining(90))
   if c.get('publication_quality_ok'):cards.append(c)
   elif source.get('pre_generation_material_update'):
    raise RuntimeError('selected validated material update failed publication-quality contract')
   else:fail.append({'source_index':a.get('source_index'),'reason':c.get('publication_quality_reasons',[])})
  except Exception as e:
   if source.get('pre_generation_material_update'):
    raise RuntimeError(f'selected validated material update failed writer path: {type(e).__name__}: {e}') from e
   fail.append({'source_index':a.get('source_index'),'reason':[type(e).__name__]})
 return {'category_key':category_key,'category_label':category_label,'hero':hero,'cards':cards,'assignment_editor':{'model':ASSIGNMENT_EDITOR_MODEL,'writer_model':WRITER_MODEL,'rejected':plan.get('rejected',[]),'writer_failures':fail,'duration_seconds':round(time.perf_counter()-started,3)}}
