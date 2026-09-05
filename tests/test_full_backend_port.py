import json
from types import SimpleNamespace
from plain_engine.article_quality import publication_quality
from plain_engine.assignment_pipeline import run_live_assignment_category
from plain_engine.generation_cache import PersistentGenerationCache, CACHE_MISS
from plain_engine.image_authority import image_rejection_reason, FallbackRotator
from plain_engine.source_recovery import focus_extracted_source_text

class FakeMessages:
    def __init__(self,responses):self.responses=list(responses);self.prompts=[]
    def create(self,**kwargs):self.prompts.append(kwargs);return SimpleNamespace(content=[SimpleNamespace(text=self.responses.pop(0))])
class FakeClient:
    def __init__(self,responses):self.messages=FakeMessages(responses)
    def with_options(self,**kwargs):return self
def words(prefix,n):return ' '.join(f'{prefix}{i}' for i in range(n))
def test_image_authority_rejects_brand_assets():
    assert image_rejection_reason('https://example.com/assets/site-logo.png')
    assert not image_rejection_reason('https://example.com/photos/capitol-hearing-1200x675.jpg')
def test_fallback_rotation_persists(tmp_path):
    d=tmp_path/'images'/'fallback';d.mkdir(parents=True)
    for n in ('world-1.jpg','world-2.jpg','world-3.jpg'):(d/n).write_bytes(b'x')
    r=FallbackRotator(tmp_path,'https://plainnews.app');assert r.choose('world','headline')[0];r.save();assert (tmp_path/'data'/'editorial-image-rotation.json').exists()
def test_source_focus_repair_discards_unrelated_prefix():
    prefix=' '.join(['Weather coverage continues across the region with forecasts and traffic updates.']*35);core='SpaceX Starship launched from Texas after federal regulators approved the flight. '*12;text=prefix+' '+core
    focused=focus_extracted_source_text(text,{'title':'SpaceX Starship launches from Texas after regulators approve flight'});assert len(focused.split())<len(text.split());assert 'SpaceX' in focused
def test_assignment_editor_writer_preserves_exact_source_binding():
    source_text='The Federal Reserve announced a policy decision in Washington on Friday affecting interest rates. '+words('fact',210)
    sources=[{'title':'Wrong story','summary':words('wrong',160),'article_text':words('wrong',160),'link':'https://a.example/1','source_quality':'full','published':'Fri, 04 Sep 2026 12:00:00 GMT'},{'title':'Federal Reserve announces rate decision','summary':source_text[:500],'article_text':source_text,'link':'https://b.example/2','source_quality':'full','published':'Fri, 04 Sep 2026 16:00:00 GMT','publisher_name':'Reuters'}]
    body=('The Federal Reserve announced a policy decision in Washington on Friday, changing its benchmark interest-rate stance and setting the immediate direction for monetary policy. Officials described the action and its effective timing in the decision released Friday.\n\n'+"The central bank's decision followed its scheduled policy meeting and applies to its benchmark rate framework. "*8+'\n\n'+'Markets and borrowers watch the benchmark because it influences financing conditions across the economy. '*7)
    client=FakeClient([json.dumps({'hero':{'source_index':2,'angle':'rate decision','urgency_score':8},'cards':[]}),json.dumps({'headline':'Federal Reserve announces benchmark rate decision','body':body})]);result=run_live_assignment_category(client,'business','Business',sources,card_count=0);hero=result['hero'];assert hero['source_index']==2;assert hero['link']=='https://b.example/2';assert 'https://a.example/1' not in client.messages.prompts[1]['messages'][0]['content']
def test_quality_requires_grounded_source():
    ok,reasons=publication_quality({'headline':'Federal agency announces national rule','body':'Short body.','article_text':'tiny'},hero=True);assert not ok;assert any('source_under' in x for x in reasons)
def test_generation_cache_roundtrip(tmp_path):
    c=PersistentGenerationCache(tmp_path/'c.json');assert c.get('source_text','x') is CACHE_MISS;c.put('source_text','x',{'text':'hello'},ttl_seconds=60);c.save();assert PersistentGenerationCache(tmp_path/'c.json').get('source_text','x')=={'text':'hello'}

def _long_body(subject='Federal officials'):
    return (
        f"{subject} announced the principal development Friday in Washington, describing the action, who it affects and the immediate factual change in the opening paragraph. "
        "The announcement identifies the responsible agency and the scope of the decision without relying on outside context.\n\n"
        + "Officials provided additional factual detail about the decision, its timing and the people or institutions directly affected. " * 10
        + "\n\n"
        + "The source also described the background necessary to understand how the new development changes the previously reported situation. " * 9
    )


def test_assignment_writer_carries_validated_material_update_authority():
    from plain_engine.assignment_pipeline import run_live_assignment_category
    source_text = "Federal officials confirmed a material new development Friday. " + words('detail', 220)
    sources = [{
        'title': 'Federal officials confirm new development',
        'summary': source_text[:800],
        'article_text': source_text,
        'link': 'https://example.com/update',
        'source_quality': 'full',
        'published': 'Fri, 04 Sep 2026 16:00:00 GMT',
        'publisher_name': 'Reuters',
        'pre_generation_material_update': True,
        'pre_generation_material_update_canonical_slug': '2026-09-03-existing-story',
        'material_update_confidence': .96,
        'material_update_novel_facts': ['official confirmation'],
        'semantic_material_update_decision': {
            'action': 'update_existing_canonical',
            'same_real_world_event': True,
            'material_new_update': True,
            'selected_candidate_slug': '2026-09-03-existing-story',
            'confidence': .96,
        },
        'canonical_context': {'slug':'2026-09-03-existing-story','headline':'Federal officials opened investigation earlier this year','body':'Federal officials opened the existing federal investigation earlier this year. '+words('prior',160)},
    }]
    client = FakeClient([
        json.dumps({'hero':{'source_index':1,'angle':'confirmed development','urgency_score':8},'cards':[]}),
        json.dumps({'headline':'Federal officials confirm material new development','body':('Federal officials confirmed Friday a material new development in the existing federal investigation, adding the official confirmation to the case first opened earlier this year. The confirmation changes the current status of that investigation.\n\n' + 'Officials provided additional factual detail about the investigation, its timing and the institutions directly affected. ' * 10 + '\n\n' + 'The source described the newly confirmed development and the background needed to understand the case. ' * 9)}),
    ])
    result = run_live_assignment_category(client, 'us', 'U.S.', sources, card_count=0)
    hero = result['hero']
    assert hero['pre_generation_material_update'] is True
    assert hero['pre_generation_material_update_canonical_slug'] == '2026-09-03-existing-story'
    assert hero['semantic_material_update_decision']['material_new_update'] is True


def test_selected_material_update_card_creates_hidden_commit_receipt():
    from scripts.generate import selected_material_update_commit_entries
    decision = {
        'action': 'update_existing_canonical',
        'same_real_world_event': True,
        'material_new_update': True,
        'selected_candidate_slug': '2026-09-03-existing-story',
        'confidence': .94,
    }
    card = {
        'headline':'Confirmed update', 'body':_long_body(), 'article_text':words('source',180),
        'publication_quality_ok':True, 'editorial_eligible':True,
        'pre_generation_material_update':True,
        'pre_generation_material_update_canonical_slug':'2026-09-03-existing-story',
        'semantic_material_update_decision':decision, 'material_update_confidence':.94,
    }
    cats=[{'category_key':'us','category_label':'U.S.','hero':{'headline':'Other story'},'cards':[card]}]
    entries=selected_material_update_commit_entries(cats)
    assert len(entries)==1
    assert entries[0][2]['_material_update_commit_only'] is True
    assert entries[0][2]['_material_update_selection_surface']=='us:card'


def test_pre_generation_materiality_suppresses_no_change_and_stamps_update(monkeypatch, tmp_path):
    import scripts.generate as gen
    monkeypatch.setattr(gen, 'OUTPUT_DIR', tmp_path)
    decisions = iter([
        {'action':gen.ACTION_DUPLICATE,'selected_candidate_slug':'old-a','confidence':.97,'novel_facts':[],'reason':'same facts'},
        {'action':gen.ACTION_UPDATE,'selected_candidate_slug':'old-b','confidence':.95,'novel_facts':['new ruling'],'reason':'same event with new ruling','same_real_world_event':True,'material_new_update':True},
    ])
    monkeypatch.setattr(gen, '_pre_generation_semantic_decision', lambda *a, **k: next(decisions))
    source_sets={'us':[
        {'title':'Same old story','link':'https://x/a','summary':words('same',100)},
        {'title':'Story advances with ruling','link':'https://x/b','summary':words('new',100)},
    ]}
    archive=[
        {'slug':'old-a','headline':'Same old story','body':words('old',160)},
        {'slug':'old-b','headline':'Earlier story','body':words('prior',160)},
    ]
    report=gen.apply_pre_generation_materiality(source_sets,archive,object(),object())
    assert len(source_sets['us'])==1
    assert source_sets['us'][0]['pre_generation_material_update'] is True
    assert source_sets['us'][0]['pre_generation_material_update_canonical_slug']=='old-b'
    assert report['duplicates_suppressed']==1 and report['material_updates']==1
    assert (tmp_path/'pre-generation-material-update-report.json').exists()


def test_selected_material_update_writer_failure_is_not_silently_dropped():
    import pytest
    from plain_engine.assignment_pipeline import run_live_assignment_category
    hero_source={'title':'National policy announcement','summary':words('hero',180),'article_text':words('hero',180),'link':'https://x/hero','source_quality':'full','published':'Fri, 04 Sep 2026 16:00:00 GMT'}
    update_source={'title':'Existing story materially advances','summary':words('update',180),'article_text':words('update',180),'link':'https://x/update','source_quality':'full','published':'Fri, 04 Sep 2026 16:05:00 GMT','pre_generation_material_update':True,'pre_generation_material_update_canonical_slug':'old','semantic_material_update_decision':{'action':'update_existing_canonical','same_real_world_event':True,'material_new_update':True,'selected_candidate_slug':'old'}}
    client=FakeClient([
        json.dumps({'hero':{'source_index':1,'angle':'policy','urgency_score':8},'cards':[{'source_index':2,'angle':'new development','urgency_score':7}]}),
        json.dumps({'headline':'Federal agency announces national policy action','body':_long_body()}),
        json.dumps({'headline':'Existing story materially advances','teaser':'New development','body':'Too short.'}),
    ])
    with pytest.raises(RuntimeError, match='validated material update'):
        run_live_assignment_category(client,'us','U.S.',[hero_source,update_source],card_count=1)

def test_national_headline_claims_must_be_in_lead():
    from plain_engine.article_quality import lead_headline_integrity
    item={
        'headline':'Florida voters face $22 million Measure 3 decision',
        'body':'Voters will decide a statewide proposal in November, according to election officials, after lawmakers completed the ballot process. The measure has drawn attention across the state.\n\nMore background follows.',
    }
    issues=lead_headline_integrity(item)
    assert 'headline_money_claim_missing_from_lead' in issues
    assert 'headline_named_measure_missing_from_lead' in issues


def test_material_update_lead_requires_old_event_and_new_development():
    from plain_engine.article_quality import lead_headline_integrity
    item={
        'headline':'Agency confirms court ruling in long-running federal case',
        'body':'The agency confirmed Friday that a court issued a new ruling affecting the case and outlined the immediate legal result for the parties involved. The ruling takes effect immediately.\n\nMore follows.',
        'pre_generation_material_update':True,
        'canonical_context':{'headline':'Federal agency opened investigation into Acme merger','body':'The Federal Trade Commission opened an investigation into the Acme merger earlier this year.'},
        'material_update_novel_facts':['court issued a new ruling'],
    }
    issues=lead_headline_integrity(item)
    assert 'original_event_context_missing' in issues
