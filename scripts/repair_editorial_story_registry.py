#!/usr/bin/env python3
"""Converge deterministic story-registry repair before publication."""
from __future__ import annotations
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from plain_engine.registry_repair import repair_registry_payload
PATH=ROOT/'story-registry.json'
def main():
 if not PATH.exists():print('Story registry: first run, nothing to repair.');return
 try:p=json.loads(PATH.read_text(encoding='utf-8'))
 except Exception as e:raise SystemExit(f'Story registry invalid: {e}')
 changed=False;passes=0
 for i in range(16):
  r=repair_registry_payload(p);passes=i+1;changed=changed or bool(r.changed)
  if not r.changed:break
 else:raise SystemExit('Story registry repair did not converge within 16 passes')
 if changed:
  tmp=PATH.with_suffix('.json.tmp');tmp.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.replace(tmp,PATH)
 print(json.dumps({'changed':changed,'repair_passes':passes,'active_stories':len(p.get('stories',{}) or {})}))
if __name__=='__main__':main()
