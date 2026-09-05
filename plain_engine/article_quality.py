"""TCT-derived publication quality and lead/headline integrity contracts."""
from __future__ import annotations
import json,re
from datetime import datetime,timezone
from pathlib import Path
MIN_HERO_BODY_WORDS=120;MIN_CARD_BODY_WORDS=90;MIN_SOURCE_WORDS=80
def word_count(t):return len(re.findall(r"\b[\w’'-]+\b",str(t or '')))
def paragraph_count(t):return len([p for p in re.split(r'\n\s*\n',str(t or '')) if word_count(p)>=8])
def sentence_count(t):return len([s for s in re.split(r'(?<=[.!?])\s+',str(t or '').strip()) if word_count(s)>=5])
def first_paragraph(t):
 v=str(t or '').strip();return re.split(r'\n\s*\n',v,maxsplit=1)[0] if v else ''
_US_STATES = {
 "alabama","alaska","arizona","arkansas","california","colorado","connecticut","delaware","florida","georgia","hawaii","idaho","illinois","indiana","iowa","kansas","kentucky","louisiana","maine","maryland","massachusetts","michigan","minnesota","mississippi","missouri","montana","nebraska","nevada","new hampshire","new jersey","new mexico","new york","north carolina","north dakota","ohio","oklahoma","oregon","pennsylvania","rhode island","south carolina","south dakota","tennessee","texas","utah","vermont","virginia","washington","west virginia","wisconsin","wyoming","district of columbia"
}
_MEASURE_RE = re.compile(r"\b(?:amendment|proposition|measure|resolution|ordinance|referendum|initiative|house bill|senate bill|bill)\s+(?:no\.?\s*)?[A-Z0-9-]+\b", re.I)

def _money_claims(text):
 value=re.sub(r'[-_]',' ',str(text or '').casefold());out=set();mult={'billion':1_000_000_000,'b':1_000_000_000,'million':1_000_000,'m':1_000_000,'thousand':1_000,'k':1_000}
 for m in re.finditer(r'(?<![a-z0-9])\$?\s*(\d+(?:\.\d+)?)\s*(billion|million|thousand|[bmk])\b(?:\s+dollars?)?',value,re.I):
  try:out.add(int(round(float(m.group(1))*mult[m.group(2).casefold()])))
  except Exception:pass
 for m in re.finditer(r'(?<![a-z0-9])\$\s*(\d[\d,]*(?:\.\d+)?)',value):
  suffix=value[m.end():]
  if re.match(r'\s*(?:billion|million|thousand|[bmk])\b',suffix,re.I):continue
  try:out.add(int(round(float(m.group(1).replace(',','')))))
  except Exception:pass
 return out

def _percent_claims(text):
 return {m.group(1).rstrip('0').rstrip('.') for m in re.finditer(r'\b(\d+(?:\.\d+)?)\s*%',str(text or ''))}

def _jurisdiction_claims(text):
 low=re.sub(r'\s+',' ',str(text or '').casefold());claims={state for state in _US_STATES if re.search(rf'\b{re.escape(state)}\b',low)}
 if re.search(r'\b(?:u\.s\.|united states|federal)\b',low):claims.add('united states/federal')
 return claims

def _measure_claims(text):
 return {re.sub(r'\s+',' ',m.group(0).casefold()).strip() for m in _MEASURE_RE.finditer(str(text or ''))}

def _context_tokens(text):
 stop={'about','after','before','from','into','with','without','that','this','their','there','would','could','should','news','report','reports','says','said','update','latest','today','officials'}
 return {t for t in re.findall(r'[a-z0-9]+',str(text or '').casefold()) if len(t)>=4 and t not in stop}

def _material_update_lead_integrity(item,lead):
 if not item.get('pre_generation_material_update'):return []
 issues=[];ctx=item.get('canonical_context') or {};canonical=str(ctx.get('headline') or '')+' '+str(ctx.get('body') or '')
 ct=_context_tokens(canonical);lt=_context_tokens(lead)
 if ct and len(ct&lt)<min(2,len(ct)):issues.append('original_event_context_missing')
 novel=' '.join(str(x) for x in (item.get('material_update_novel_facts') or []));nt=_context_tokens(novel)-ct
 if nt and not (nt&lt):issues.append('new_development_missing')
 return issues

def lead_headline_integrity(item):
 h=str(item.get('headline') or '');lead=first_paragraph(item.get('body',''));issues=[]
 if word_count(h)<4:issues.append('weak_or_missing_headline')
 if word_count(lead)<20:issues.append('lead_too_thin')
 headline_money,lead_money=_money_claims(h),_money_claims(lead)
 if headline_money-lead_money:issues.append('headline_money_claim_missing_from_lead')
 headline_pct,lead_pct=_percent_claims(h),_percent_claims(lead)
 if headline_pct-lead_pct:issues.append('headline_percentage_claim_missing_from_lead')
 headline_jur,lead_jur=_jurisdiction_claims(h),_jurisdiction_claims(lead)
 if headline_jur-lead_jur:issues.append('headline_jurisdiction_missing_from_lead')
 headline_measures,lead_measures=_measure_claims(h),_measure_claims(lead)
 if headline_measures-lead_measures:issues.append('headline_named_measure_missing_from_lead')
 entities=re.findall(r'\b[A-Z][A-Za-z0-9&.\'’-]+(?:\s+[A-Z][A-Za-z0-9&.\'’-]+){0,3}\b',h)[:8]
 if entities and not any(e.casefold() in lead.casefold() for e in entities[:4]):issues.append('headline_entity_missing_from_lead')
 st=str(item.get('source_title') or '');names=re.findall(r'\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b',st)
 if names and re.search(rf'\b{re.escape(names[0].split()[-1])}\b',lead) and names[0] not in lead:issues.append('person_first_reference_not_full_name')
 issues += _material_update_lead_integrity(item,lead)
 return sorted(set(issues))
_FOCUS_STOP={"about","after","before","from","into","with","without","that","this","their","there","would","could","should","news","report","reports","says","said","update","latest","today"}
def _focus_tokens(text):
 return {t for t in re.findall(r"[a-z0-9]+",str(text or '').casefold()) if len(t)>=4 and t not in _FOCUS_STOP}
def _opening(text,max_sentences=2,max_words=80):
 normalized=re.sub(r"\s+"," ",str(text or '')).strip()
 parts=[x.strip() for x in re.split(r"(?<=[.!?])\s+",normalized) if x.strip()]
 return " ".join((" ".join(parts[:max_sentences]) if parts else normalized).split()[:max_words])
def source_focus_diagnostics(item):
 """TCT-style fail-closed guard against writer/source focus drift."""
 source_title=str(item.get('source_title') or '');source_text=str(item.get('article_text') or item.get('source_summary') or '')
 generated_headline=str(item.get('headline') or '');generated_lead=_opening(first_paragraph(item.get('body','')))
 st=_focus_tokens(source_title);sl=_focus_tokens(_opening(source_text));gh=_focus_tokens(generated_headline);gl=_focus_tokens(generated_lead)
 required=bool(generated_headline and generated_lead and len(st)>=5 and len(sl)>=8 and word_count(source_text)>=35)
 if not required:return {'required':False,'passed':True,'missing':[]}
 title_overlap=len(gh&st)/max(1,min(len(gh),len(st)));lead_overlap=len(gl&sl)/max(1,min(len(gl),len(sl)))
 drifted=title_overlap<.38 and len(gh&st)<=3 and lead_overlap<.30
 return {'required':True,'passed':not drifted,'title_overlap':round(title_overlap,3),'lead_overlap':round(lead_overlap,3),'missing':['generated_copy_drifted_from_source_focus'] if drifted else []}

_MATERIAL_UPDATE_REPAIRABLE_REASONS = {
    "weak_or_missing_headline",
    "lead_too_thin",
    "headline_money_claim_missing_from_lead",
    "headline_percentage_claim_missing_from_lead",
    "headline_jurisdiction_missing_from_lead",
    "headline_named_measure_missing_from_lead",
    "headline_entity_missing_from_lead",
    "person_first_reference_not_full_name",
    "original_event_context_missing",
    "new_development_missing",
    "insufficient_article_structure",
}

def _repairable_material_update_reason(reason):
    reason=str(reason or "").strip()
    return bool(reason in _MATERIAL_UPDATE_REPAIRABLE_REASONS or reason.startswith("body_under_"))

def defer_protected_material_update_quality_failure(item,reasons,*,guard="publication_quality"):
    """Keep a validated update alive only when the failed rule is safely repairable.

    TCT's production pipeline does not throw away a target-bound material update just
    because the first writer pass is thin or lacks enough old-event context.  Those
    defects are repaired later by the canonical composer, which receives both the
    existing article and the exact incoming source.  Source drift and weak source
    evidence are deliberately *not* repairable here and still fail closed.
    """
    if not isinstance(item,dict) or not item.get("pre_generation_material_update"):
        return False
    slug=str(item.get("pre_generation_material_update_canonical_slug") or "").strip()
    decision=item.get("semantic_material_update_decision") or {}
    ctx=item.get("canonical_context") or {}
    valid_authority=bool(
        slug
        and isinstance(decision,dict)
        and decision.get("action")=="update_existing_canonical"
        and decision.get("same_real_world_event") is True
        and decision.get("material_new_update") is True
        and str(decision.get("selected_candidate_slug") or "").strip()==slug
        and (not isinstance(ctx,dict) or not ctx.get("slug") or str(ctx.get("slug"))==slug)
    )
    if not valid_authority:
        return False
    reasons=[str(r) for r in (reasons or []) if str(r)]
    if not reasons or not all(_repairable_material_update_reason(r) for r in reasons):
        return False
    item["_force_material_update_recomposition"]=True
    item["publication_quality_deferred_for_material_update"]=True
    item.setdefault("protected_material_update_quality_holds",[]).append({
        "guard":str(guard or "publication_quality"),
        "reasons":sorted(set(reasons)),
    })
    return True

def protected_material_update_pending_recomposition(item):
    return bool(
        isinstance(item,dict)
        and item.get("_force_material_update_recomposition")
        and item.get("pre_generation_material_update")
        and item.get("pre_generation_material_update_canonical_slug")
    )

def publication_quality(item,*,hero=False):
 reasons=[];body=str(item.get('body') or '');source=str(item.get('article_text') or item.get('source_summary') or '');minimum=MIN_HERO_BODY_WORDS if hero else MIN_CARD_BODY_WORDS
 if word_count(body)<minimum:reasons.append(f'body_under_{minimum}_words')
 if word_count(source)<MIN_SOURCE_WORDS:reasons.append(f'source_under_{MIN_SOURCE_WORDS}_words')
 if paragraph_count(body)<2 and sentence_count(body)<5:reasons.append('insufficient_article_structure')
 reasons+=lead_headline_integrity(item);reasons+=source_focus_diagnostics(item).get('missing',[]);return not reasons,sorted(set(reasons))
def enforce_category_quality(category):
 hero=category.get('hero');good=[]
 for c in [x for x in category.get('cards',[]) if isinstance(x,dict)]:
  ok,r=publication_quality(c,hero=False);c['publication_quality_ok']=ok;c['publication_quality_reasons']=r
  if ok:good.append(c)
 if isinstance(hero,dict):
  ok,r=publication_quality(hero,hero=True);hero['publication_quality_ok']=ok;hero['publication_quality_reasons']=r
  if not ok and protected_material_update_pending_recomposition(hero):
   # Do not swap away a validated update that has been explicitly queued for
   # canonical recomposition. write_archives() repairs this exact hero before the
   # public index/data surfaces are rendered.
   hero['publication_quality_deferred_for_material_update']=True
  elif not ok:
   for i,c in enumerate(good):
    hok,hr=publication_quality(c,hero=True)
    if hok:c['publication_quality_ok']=True;c['publication_quality_reasons']=hr;c['promoted_for_quality']=True;category['hero']=c;good.pop(i);break
 category['cards']=good;return category
def write_quality_report(categories,path):
 rows=[]
 for c in categories:
  h=c.get('hero',{});rows.append({'category':c.get('category_key'),'hero':{'headline':h.get('headline'),'ok':h.get('publication_quality_ok'),'reasons':h.get('publication_quality_reasons',[])},'cards':[{'headline':x.get('headline'),'ok':x.get('publication_quality_ok'),'reasons':x.get('publication_quality_reasons',[])} for x in c.get('cards',[])]})
 report={'generated_at':datetime.now(timezone.utc).isoformat(),'categories':rows};Path(path).write_text(json.dumps(report,ensure_ascii=False,indent=2));return report
