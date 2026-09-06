"""Sonnet 5 assignment editor -> exact-source Sonnet 4.5 writer."""
from __future__ import annotations
import json,re,time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from .article_quality import publication_quality,defer_protected_material_update_quality_failure
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
  'classified_categories':list(s.get('classified_categories') or []),'category_fit_hint':str(s.get('category_fit_hint') or ''),
  'canonical_context':s.get('canonical_context') or {},
  'material_update_candidate':bool(s.get('pre_generation_material_update')),
  'material_update_canonical_slug':str(s.get('pre_generation_material_update_canonical_slug') or ''),
  'material_update_confidence':float(s.get('material_update_confidence') or 0.0),
  'material_update_novel_facts':list(s.get('material_update_novel_facts') or []),
 }

_HARD_REJECTION_TERMS = (
    "duplicate", "same event", "same story", "off-category", "off category", "wrong category",
    "category misfit", "listicle", "promotional", "promotion", "advertorial", "opinion-only",
    "opinion only", "stale", "discovery-only", "discovery only", "too thin", "unsafe", "non-news",
)


def _title_tokens(value):
    stop = {"the","and","for","with","from","after","before","into","over","this","that","news","says","said","new","latest","update"}
    return {t for t in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if len(t) >= 3 and t not in stop}


def _same_source_story(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    lu = str(left.get("link") or left.get("source_url") or "").split("?", 1)[0].rstrip("/").casefold()
    ru = str(right.get("link") or right.get("source_url") or "").split("?", 1)[0].rstrip("/").casefold()
    if lu and ru and lu == ru:
        return True
    lc = (left.get("canonical_context") or {}).get("slug") if isinstance(left.get("canonical_context"), dict) else ""
    rc = (right.get("canonical_context") or {}).get("slug") if isinstance(right.get("canonical_context"), dict) else ""
    if lc and rc and str(lc) == str(rc):
        return True
    a, b = _title_tokens(left.get("title", "")), _title_tokens(right.get("title", ""))
    if len(a) < 4 or len(b) < 4:
        return False
    shared = a & b
    containment = len(shared) / max(1, min(len(a), len(b)))
    return len(shared) >= 4 and containment >= 0.70


def _hard_rejected_indexes(plan):
    out = set()
    for row in (plan.get("rejected") or []):
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("source_index"))
        except Exception:
            continue
        reason = str(row.get("reason") or "").casefold()
        if any(term in reason for term in _HARD_REJECTION_TERMS):
            out.add(index)
    return out


def _backfill_assignments(sources, *, used_indexes, selected_sources, rejected_indexes, limit):
    """Return safe deterministic card assignments to preserve Plain's section depth."""
    rows = []
    chosen = list(selected_sources or [])
    used = set(used_indexes or set())
    rejected = set(rejected_indexes or set())
    for i, source in enumerate(sources, 1):
        if len(rows) >= max(0, int(limit or 0)):
            break
        if i in used or i in rejected:
            continue
        rec = _record(i, source)
        age = _age(source.get("published"))
        if rec["source_quality"] not in {"full", "summary"} or rec["source_word_count"] < 80:
            continue
        if age is not None and age > 72:
            continue
        if any(_same_source_story(source, prior) for prior in chosen):
            continue
        score = 8 if source.get("pre_generation_material_update") else max(4, 7 - (len(rows) // 3))
        rows.append({
            "source_index": i,
            "angle": str(source.get("title") or "").strip(),
            "urgency_score": score,
            "depth_backfill": True,
        })
        used.add(i)
        chosen.append(source)
    return rows


def build_assignment_editor_packet(category_key,category_label,sources):return json.dumps({'publication':'Plain','audience':'nationwide U.S. general-news audience','category_key':category_key,'category_label':category_label,'category_rule':category_rule(category_key),'sources':[_record(i,s) for i,s in enumerate(sources,1)]},ensure_ascii=False)
def run_assignment_editor(client,category_key,category_label,sources,*,card_count=DEFAULT_CARD_COUNT,timeout_seconds=60):
 prompt=f'''You are Plain's assignment editor. SELECT exact source indices and angles; DO NOT write article copy.
Plain is a nationwide U.S. general-news product. Category rule: {category_rule(category_key)}
The source packet includes classified_categories/category_fit_hint. Treat these as advisory routing signals, not automatic authority: prefer positive matches, and use a cross-tagged source only when its actual source text clearly belongs in this section.
Reject routine local incidents without national significance, promotions/listicles, opinion-only items, thin/discovery-only sources, stale reprints and category misfits. Choose one hero only from hero_eligible=true and then {card_count} distinct cards whenever at least {card_count} additional safe, current, nonduplicate sources exist. Return fewer than {card_count} cards ONLY when the packet genuinely contains fewer safe publishable stories; lower priority alone is not a reason to leave the section thin. A source with material_update_candidate=true has already passed a bounded semantic same-event/material-new-facts gate against an existing Plain canonical; treat it as a protected current development and strongly prefer selecting it unless it is unsafe or outside this category. Do not assign two reports of the same event. For every omitted source you consider unsafe, stale, duplicate, off-category, promotional/opinion, or too thin, include it in rejected with a concise reason.
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
 target='330-430 words in 4-5 paragraphs' if hero else '90-120 words in exactly 2 concise paragraphs. Summarize only the most important confirmed elements; this is a card summary, NOT a full article. Do not exceed 130 words.';schema='{"headline":"...","body":"..."}' if hero else '{"headline":"...","teaser":"...","body":"..."}'
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
def run_live_assignment_category(client,category_key,category_label,sources,*,card_count=DEFAULT_CARD_COUNT,timeout_seconds=240):
 started=time.perf_counter();deadline=started+max(1,float(timeout_seconds))
 def remaining(default):
  left=deadline-time.perf_counter()
  if left<=1:raise TimeoutError(f'category budget exhausted for {category_label}')
  return min(default,left)
 plan=run_assignment_editor(client,category_key,category_label,sources,card_count=card_count,timeout_seconds=remaining(60))
 ha=plan['hero'];hero_index=int(ha['source_index']);hero_source=sources[hero_index-1]
 hero=run_assignment_writer(client,category_key=category_key,category_label=category_label,source=hero_source,assignment=ha,hero=True,timeout_seconds=remaining(90))
 protected_repairs=[]
 if hero_source.get('pre_generation_material_update') and not hero.get('publication_quality_ok'):
  if defer_protected_material_update_quality_failure(hero,hero.get('publication_quality_reasons',[]),guard='assignment_writer_hero'):
   protected_repairs.append({'surface':'hero','source_index':ha.get('source_index'),'reasons':list(hero.get('publication_quality_reasons') or []),'canonical_slug':hero.get('pre_generation_material_update_canonical_slug','')})
  else:
   raise RuntimeError('selected validated material update hero failed non-repairable publication-quality contract: '+','.join(hero.get('publication_quality_reasons') or ['unknown']))
 cards=[];fail=[];hidden_repairs=[];used={hero_index};selected_sources=[hero_source];editor_cards=list(plan.get('cards',[]));editor_selected_count=len(editor_cards);backfill_attempts=0
 rejected_indexes=_hard_rejected_indexes(plan)
 queue=list(editor_cards)
 queued_indexes=set()
 queued_sources=[]
 for assignment in editor_cards:
  try:index=int(assignment.get('source_index'))
  except Exception:continue
  if 1<=index<=len(sources):queued_indexes.add(index);queued_sources.append(sources[index-1])
 initial_backfill=_backfill_assignments(sources,used_indexes=used|queued_indexes,selected_sources=[hero_source,*queued_sources],rejected_indexes=rejected_indexes,limit=max(0,card_count-len(queue))+6)
 queue.extend(initial_backfill)
 max_card_attempts=min(max(0,len(sources)-1),card_count+6);attempts=0
 for a in queue:
  if len(cards)>=card_count or attempts>=max_card_attempts:break
  try:i=int(a.get('source_index'))
  except Exception:continue
  if i<1 or i>len(sources) or i in used:continue
  source=sources[i-1]
  if any(_same_source_story(source,prior) for prior in selected_sources):continue
  used.add(i);attempts+=1
  if a.get('depth_backfill'):backfill_attempts+=1
  try:
   c=run_assignment_writer(client,category_key=category_key,category_label=category_label,source=source,assignment=a,hero=False,timeout_seconds=remaining(90))
   if c.get('publication_quality_ok'):
    cards.append(c);selected_sources.append(source)
   elif source.get('pre_generation_material_update'):
    if defer_protected_material_update_quality_failure(c,c.get('publication_quality_reasons',[]),guard='assignment_writer_card'):
     hidden_repairs.append(c);selected_sources.append(source)
     protected_repairs.append({'surface':'card_hidden_commit','source_index':a.get('source_index'),'reasons':list(c.get('publication_quality_reasons') or []),'canonical_slug':c.get('pre_generation_material_update_canonical_slug','')})
    else:
     raise RuntimeError('selected validated material update failed non-repairable publication-quality contract: '+','.join(c.get('publication_quality_reasons') or ['unknown']))
   else:
    fail.append({'source_index':a.get('source_index'),'reason':c.get('publication_quality_reasons',[])})
  except Exception as e:
   if source.get('pre_generation_material_update'):
    raise RuntimeError(f'selected validated material update failed writer path: {type(e).__name__}: {e}') from e
   fail.append({'source_index':a.get('source_index'),'reason':[type(e).__name__]})
 if len(cards)<card_count and attempts<max_card_attempts:
  extra=_backfill_assignments(sources,used_indexes=used,selected_sources=selected_sources,rejected_indexes=rejected_indexes,limit=max_card_attempts-attempts)
  for a in extra:
   if len(cards)>=card_count or attempts>=max_card_attempts:break
   i=int(a['source_index']);source=sources[i-1];used.add(i);attempts+=1;backfill_attempts+=1
   try:
    c=run_assignment_writer(client,category_key=category_key,category_label=category_label,source=source,assignment=a,hero=False,timeout_seconds=remaining(90))
    if c.get('publication_quality_ok'):
     cards.append(c);selected_sources.append(source)
    elif source.get('pre_generation_material_update'):
     if defer_protected_material_update_quality_failure(c,c.get('publication_quality_reasons',[]),guard='assignment_writer_card_backfill'):
      hidden_repairs.append(c);selected_sources.append(source);protected_repairs.append({'surface':'card_hidden_commit','source_index':a.get('source_index'),'reasons':list(c.get('publication_quality_reasons') or []),'canonical_slug':c.get('pre_generation_material_update_canonical_slug','')})
     else:raise RuntimeError('selected validated material update failed non-repairable publication-quality contract: '+','.join(c.get('publication_quality_reasons') or ['unknown']))
    else:fail.append({'source_index':a.get('source_index'),'reason':c.get('publication_quality_reasons',[])})
   except Exception as e:
    if source.get('pre_generation_material_update'):raise RuntimeError(f'selected validated material update failed writer path: {type(e).__name__}: {e}') from e
    fail.append({'source_index':a.get('source_index'),'reason':[type(e).__name__]})
 return {
  'category_key':category_key,'category_label':category_label,'hero':hero,'cards':cards,
  'protected_material_update_commits':hidden_repairs,
  'assignment_editor':{
   'model':ASSIGNMENT_EDITOR_MODEL,'writer_model':WRITER_MODEL,'rejected':plan.get('rejected',[]),
   'writer_failures':fail,'protected_material_update_repairs':protected_repairs,
   'editor_selected_card_count':editor_selected_count,'section_depth_target':card_count,
   'section_depth_accepted_cards':len(cards),'section_depth_shortfall':max(0,card_count-len(cards)),
   'depth_backfill_writer_attempts':backfill_attempts,'writer_card_attempts':attempts,
   'duration_seconds':round(time.perf_counter()-started,3),
  },
 }
