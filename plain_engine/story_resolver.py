"""Resolver v2: conservative fact-based persistent story resolution."""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

_WORD_RE=re.compile(r"[a-z0-9]+")
_STOP={"the","and","for","with","from","after","before","into","county","news","update","says","said"}

def _norm(v): return " ".join(str(v or "").casefold().split())
def _set(vs): return {_norm(v) for v in vs if _norm(v)}
def _tokens(v): return {x for x in _WORD_RE.findall(_norm(v)) if len(x)>=3 and x not in _STOP}
def _overlap(a,b): return len(a&b)/min(len(a),len(b)) if a and b else 0.0

def _category(event_types:set[str], title:str)->str:
    blob=" ".join(event_types)+" "+_norm(title)
    if any(x in blob for x in ("crash","arrest","shoot","missing person","animal rescue","crime")): return "crime_public_safety"
    if any(x in blob for x in ("meeting","government","budget","ordinance","commission","council")): return "government"
    if any(x in blob for x in ("baseball","football","basketball","game","mets","cardinals")): return "sports"
    if any(x in blob for x in ("storm","hurricane","tornado","weather","flood")): return "weather"
    if any(x in blob for x in ("business","development","company","store","restaurant")): return "business"
    return "general"

@dataclass(frozen=True, slots=True)
class StoryResolution:
    story_id: str|None
    merge: bool
    confidence: float
    reason: str
    decision_trace: tuple[str,...]=()

class StoryResolver:
    MERGE_THRESHOLD=0.78
    def resolve(self, *, event_key:str, title:str, facts:Iterable[str], locations:Iterable[str]=(), agencies:Iterable[str]=(), event_types:Iterable[str]=(), entities:Iterable[str]=(), published_at:datetime|None=None, stories:Iterable[Mapping[str,Any]])->StoryResolution:
        inc={"facts":_set(facts),"locations":_set(locations),"agencies":_set(agencies),"types":_set(event_types),"entities":_set(entities)}
        cat=_category(inc["types"], title); title_tokens=_tokens(title); event_tokens=_tokens(event_key.replace("-"," "))
        best=None; best_score=0.0; best_trace=("No sufficiently supported match",)
        for s in stories:
            if s.get("status")=="archived": continue
            sid=str(s.get("story_id","")).strip()
            if not sid: continue
            known={"facts":_set(s.get("facts",())),"locations":_set(s.get("locations",())),"agencies":_set(s.get("agencies",())),"types":_set(s.get("event_types",())),"entities":_set(s.get("entities",()))}
            trace=[]
            # Hard contradictions.
            if inc["locations"] and known["locations"] and not inc["locations"]&known["locations"]:
                continue
            if inc["agencies"] and known["agencies"] and not inc["agencies"]&known["agencies"]:
                continue
            inc_cat=cat; known_cat=_category(known["types"], str(s.get("canonical_title", "")))
            if inc_cat!="general" and known_cat!="general" and inc_cat!=known_cat: continue
            scores={k:_overlap(inc[k],known[k]) for k in inc}
            known_title=_tokens(str(s.get("canonical_title", ""))) | set(s.get("title_tokens",()))
            title_score=len(title_tokens&known_title)/max(1,min(len(title_tokens),len(known_title)))
            known_events=set()
            for e in s.get("events",()): known_events |= _tokens(str(e).replace("-"," "))
            event_score=len(event_tokens&known_events)/max(1,min(len(event_tokens),len(known_events)))
            anchors=sum(bool(inc[k]&known[k]) for k in ("locations","agencies","types","entities"))
            # Category-specific minimum evidence.
            if inc_cat=="crime_public_safety": eligible=(anchors>=2 and (scores["facts"]>0 or scores["entities"]>0 or event_score>=.55))
            elif inc_cat=="government": eligible=((scores["agencies"]>0 or scores["entities"]>0) and (scores["facts"]>0 or title_score>=.45))
            elif inc_cat=="sports": eligible=((scores["entities"]>0 or title_score>=.55) and (scores["facts"]>0 or event_score>=.55))
            elif inc_cat=="weather": eligible=(scores["types"]>0 and (scores["locations"]>0 or scores["entities"]>0))
            elif inc_cat=="business": eligible=(scores["entities"]>0 and (scores["locations"]>0 or title_score>=.5))
            else: eligible=(anchors>=2 and scores["facts"]>0)
            weights={"facts":.25,"locations":.18,"agencies":.18,"types":.14,"entities":.25}
            structured=sum(weights[k]*scores[k] for k in weights)
            score=.88*structured+.07*title_score+.05*event_score
            trace=[f"Category: {inc_cat}",f"Facts overlap: {scores['facts']:.2f}",f"Locations overlap: {scores['locations']:.2f}",f"Agencies overlap: {scores['agencies']:.2f}",f"Event types overlap: {scores['types']:.2f}",f"Entities overlap: {scores['entities']:.2f}",f"Title support: {title_score:.2f}",f"Event-key support: {event_score:.2f}",f"Identity anchors: {anchors}"]
            if eligible and score>best_score:
                best=sid; best_score=score; best_trace=tuple(trace)
        merge=best is not None and best_score>=self.MERGE_THRESHOLD
        if not merge:
            return StoryResolution(None,False,best_score,"Created new story: conservative merge requirements were not met",best_trace+(f"Threshold: {self.MERGE_THRESHOLD:.2f}",))
        return StoryResolution(best,True,best_score,"Merged into existing story using Resolver v2 structured evidence",best_trace+(f"Threshold passed: {self.MERGE_THRESHOLD:.2f}",))
