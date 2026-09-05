"""Persistent content-addressed generation cache for Plain."""
from __future__ import annotations
import copy, hashlib, json, os, re, threading, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CACHE_SCHEMA_VERSION = 1
CACHE_MISS = object()

def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def parse_cache_time(value):
    try: return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except Exception: return 0.0

def cache_hash(value: Any) -> str:
    raw=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str)
    return hashlib.sha256(raw.encode()).hexdigest()

def normalize_cache_url(url):
    value=str(url or '').strip()
    if not value: return ''
    try:
        parts=urlsplit(value); filtered=[]
        for k,v in parse_qsl(parts.query,keep_blank_values=True):
            low=k.lower()
            if low.startswith('utm_') or low in {'gclid','fbclid','mc_cid','mc_eid','ocid','cmpid','ref','ref_src'}: continue
            filtered.append((k,v))
        path=re.sub(r'/{2,}','/',parts.path or '/')
        return urlunsplit((parts.scheme.lower(),parts.netloc.lower(),path,urlencode(filtered,doseq=True),''))
    except Exception: return value

def source_content_hint(source):
    source=source or {}
    return cache_hash({'title':source.get('title',''),'summary':source.get('summary',''),'published':source.get('published','')})

class PersistentGenerationCache:
    LIMITS={'source_text':500,'source_resolutions':700,'classifications':2500,'categories':140,'guardian_text':400,'semantic_gate':600,'pre_generation_materiality':900,'material_updates':300}
    def __init__(self,path):
        self.path=Path(path); self.lock=threading.RLock(); self.dirty=False; self.stats=defaultdict(int); self.payload=self._empty(); self._load()
    def _empty(self): return {'schema_version':CACHE_SCHEMA_VERSION,'updated_at':'',**{k:{} for k in self.LIMITS}}
    @staticmethod
    def _valid_category_value(value):
        if not isinstance(value,dict): return False
        data=value.get('data') if isinstance(value.get('data'),dict) else value
        return isinstance(data,dict) and isinstance(data.get('hero'),dict) and bool(data['hero'].get('headline')) and isinstance(data.get('cards',[]),list)
    def _load(self):
        try:
            raw=json.loads(self.path.read_text(encoding='utf-8'))
            if raw.get('schema_version')!=CACHE_SCHEMA_VERSION: return
            for b in self.LIMITS:
                if not isinstance(raw.get(b),dict): raw[b]={}
            for key,entry in list(raw['categories'].items()):
                if not isinstance(entry,dict) or 'value' not in entry or not self._valid_category_value(entry.get('value')):
                    raw['categories'].pop(key,None); self.dirty=True; self.stats['integrity_sanitizations']+=1
            self.payload=raw
        except Exception: self.payload=self._empty()
    def reset_stats(self):
        with self.lock: self.stats=defaultdict(int)
    def get(self,bucket,key):
        with self.lock:
            entry=self.payload.get(bucket,{}).get(key)
            if not isinstance(entry,dict) or 'value' not in entry: self.stats[f'{bucket}_miss']+=1; return CACHE_MISS
            exp=parse_cache_time(entry.get('expires_at'))
            if exp and exp<=time.time(): self.payload.setdefault(bucket,{}).pop(key,None); self.dirty=True; self.stats[f'{bucket}_expired']+=1; return CACHE_MISS
            self.stats[f'{bucket}_hit']+=1; return copy.deepcopy(entry['value'])
    def put(self,bucket,key,value,ttl_seconds=None):
        now=time.time(); entry={'cached_at':datetime.fromtimestamp(now,timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z'),'expires_at':datetime.fromtimestamp(now+float(ttl_seconds),timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z') if ttl_seconds else '','value':copy.deepcopy(value)}
        with self.lock: self.payload.setdefault(bucket,{})[key]=entry; self.dirty=True; self.stats[f'{bucket}_write']+=1
    def delete(self,bucket,key,reason='invalid'):
        with self.lock:
            if self.payload.setdefault(bucket,{}).pop(key,None) is None:return False
            self.dirty=True; self.stats[f'{bucket}_deleted_{reason}']+=1; return True
    def _prune(self):
        for b,limit in self.LIMITS.items():
            entries=self.payload.setdefault(b,{})
            if len(entries)>limit:
                ordered=sorted(entries.items(),key=lambda pair:parse_cache_time((pair[1] or {}).get('cached_at')),reverse=True)
                self.payload[b]=dict(ordered[:limit])
    def save(self,force=False):
        with self.lock:
            if not self.dirty and not force:return
            self._prune(); self.payload['schema_version']=CACHE_SCHEMA_VERSION; self.payload['updated_at']=utc_now_iso(); self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_suffix(self.path.suffix+'.tmp'); tmp.write_text(json.dumps(self.payload,indent=2,ensure_ascii=False),encoding='utf-8'); os.replace(tmp,self.path); self.dirty=False
    def counts(self): return {b:len(self.payload.get(b,{})) for b in self.LIMITS}
    def summary(self): return ', '.join(f'{k}={v}' for k,v in sorted(self.stats.items()) if v) or 'no cache activity'
