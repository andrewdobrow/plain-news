#!/usr/bin/env python3
"""Sanitize restored generation cache before tests/publication."""
from __future__ import annotations
import json,re,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];DEFAULT=ROOT/'data'/'generation-cache.json'
STOP={'a','an','and','are','as','at','be','by','for','from','has','have','in','is','it','of','on','or','that','the','this','to','was','were','will','with'}
def tok(v):return {w for w in re.findall(r'[a-z0-9]+',str(v or '').casefold()) if len(w)>1 and w not in STOP}
def drift(item):
 if not isinstance(item,dict):return True
 h=str(item.get('headline') or '');body=str(item.get('body') or '');st=str(item.get('source_title') or '');src=str(item.get('article_text') or item.get('source_summary') or '')
 if not h or not body:return True
 if len(src.split())<80:return True
 ht,stt=tok(h),tok(st);lead=tok(' '.join(body.split()[:80]));slead=tok(' '.join(src.split()[:100]))
 if len(stt)>=5 and ht and len(ht&stt)/max(1,min(len(ht),len(stt)))<.25 and len(lead&slead)/max(1,min(len(lead),len(slead)))<.20:return True
 return False
def main():
 path=DEFAULT
 if not path.exists():print('Generation cache: none restored.');return
 try:p=json.loads(path.read_text())
 except Exception:path.unlink(missing_ok=True);print('Generation cache: invalid JSON removed.');return
 cats=p.get('categories') if isinstance(p.get('categories'),dict) else {};removed=0
 for k,e in list(cats.items()):
  d=((e or {}).get('value') or {}).get('data',{}) if isinstance(e,dict) else {};items=[]
  if isinstance(d.get('hero'),dict):items.append(d['hero'])
  if isinstance(d.get('cards'),list):items += [x for x in d['cards'] if isinstance(x,dict)]
  if not items or any(drift(x) for x in items):cats.pop(k,None);removed+=1
 p['categories']=cats;p['cache_integrity_version']='plain-v3-source-focus-quality'
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.json.tmp');tmp.write_text(json.dumps(p,ensure_ascii=False,indent=2));os.replace(tmp,path);print(f'Generation cache: removed {removed} unsafe category entries.')
if __name__=='__main__':main()
