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
        {'title':'Same old story','link':'https://x/a','summary':words('same',100),'source_quality':'summary'},
        {'title':'Story advances with ruling','link':'https://x/b','summary':words('new',100),'source_quality':'summary'},
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


def test_repairable_material_update_card_is_hidden_and_retained_for_canonical_recomposition():
    from plain_engine.assignment_pipeline import run_live_assignment_category
    from scripts.generate import selected_material_update_commit_entries
    hero_source={'title':'National policy announcement','summary':words('hero',180),'article_text':words('hero',180),'link':'https://x/hero','source_quality':'full','published':'Fri, 04 Sep 2026 16:00:00 GMT'}
    update_source={'title':'Existing story materially advances','summary':words('update',180),'article_text':words('update',180),'link':'https://x/update','source_quality':'full','published':'Fri, 04 Sep 2026 16:05:00 GMT','pre_generation_material_update':True,'pre_generation_material_update_canonical_slug':'old','semantic_material_update_decision':{'action':'update_existing_canonical','same_real_world_event':True,'material_new_update':True,'selected_candidate_slug':'old'}}
    client=FakeClient([
        json.dumps({'hero':{'source_index':1,'angle':'policy','urgency_score':8},'cards':[{'source_index':2,'angle':'new development','urgency_score':7}]}),
        json.dumps({'headline':'Federal agency announces national policy action','body':_long_body()}),
        json.dumps({'headline':'Existing story materially advances','teaser':'New development','body':'Too short.'}),
    ])
    result=run_live_assignment_category(client,'us','U.S.',[hero_source,update_source],card_count=1)
    assert result['cards']==[]
    assert len(result['protected_material_update_commits'])==1
    repair=result['protected_material_update_commits'][0]
    assert repair['_force_material_update_recomposition'] is True
    assert repair['publication_quality_ok'] is False
    entries=selected_material_update_commit_entries([result])
    assert len(entries)==1
    assert entries[0][2]['_material_update_commit_only'] is True
    assert entries[0][2]['_material_update_selection_surface']=='us:hidden_repair'


def test_dangerous_material_update_source_drift_still_fails_closed():
    import pytest
    from plain_engine.assignment_pipeline import run_live_assignment_category
    source_text=('Entertainment company executives confirmed a major acquisition agreement Friday in Los Angeles. ' +
                 'The transaction concerns the same entertainment company and acquisition agreement. ' + words('entertainment',170))
    update_source={
        'title':'Entertainment company confirms major acquisition agreement Friday',
        'summary':source_text[:800],'article_text':source_text,'link':'https://x/update','source_quality':'full',
        'published':'Fri, 04 Sep 2026 16:05:00 GMT','pre_generation_material_update':True,
        'pre_generation_material_update_canonical_slug':'old',
        'semantic_material_update_decision':{'action':'update_existing_canonical','same_real_world_event':True,'material_new_update':True,'selected_candidate_slug':'old'},
    }
    unrelated_body=('Astronomers reported a distant stellar observation from a telescope facility on Friday, describing a new set of measurements unrelated to corporate transactions. ' + words('astronomy',190))
    client=FakeClient([
        json.dumps({'hero':{'source_index':1,'angle':'acquisition','urgency_score':8},'cards':[]}),
        json.dumps({'headline':'Astronomers report distant stellar observation from telescope facility','body':unrelated_body}),
    ])
    with pytest.raises(RuntimeError,match='non-repairable publication-quality contract'):
        run_live_assignment_category(client,'entertainment','Entertainment',[update_source],card_count=0)


def test_repairable_material_update_hero_survives_quality_gate_for_write_barrier():
    from plain_engine.assignment_pipeline import run_live_assignment_category
    from plain_engine.article_quality import enforce_category_quality,protected_material_update_pending_recomposition
    source_text='Federal officials confirmed Friday a new development in an existing national investigation. '+words('update',200)
    source={
        'title':'Federal officials confirm new development in existing investigation','summary':source_text[:800],
        'article_text':source_text,'link':'https://x/update','source_quality':'full','published':'Fri, 04 Sep 2026 16:05:00 GMT',
        'pre_generation_material_update':True,'pre_generation_material_update_canonical_slug':'old',
        'semantic_material_update_decision':{'action':'update_existing_canonical','same_real_world_event':True,'material_new_update':True,'selected_candidate_slug':'old','novel_facts':['new development confirmed']},
        'material_update_novel_facts':['new development confirmed'],
        'canonical_context':{'slug':'old','headline':'Federal officials opened national investigation earlier','body':'Federal officials opened the national investigation earlier this year. '+words('prior',160)},
    }
    # Deliberately omit the old-event context from the writer lead. That is repairable
    # by the canonical composer and must not kill the category.
    body='Federal officials confirmed Friday a new development and released additional information about the matter. '+words('update',150)
    client=FakeClient([
        json.dumps({'hero':{'source_index':1,'angle':'confirmed development','urgency_score':8},'cards':[]}),
        json.dumps({'headline':'Federal officials confirm new development in national investigation','body':body}),
    ])
    result=run_live_assignment_category(client,'us','U.S.',[source],card_count=0)
    assert result['hero']['publication_quality_ok'] is False
    assert protected_material_update_pending_recomposition(result['hero'])
    enforce_category_quality(result)
    assert result['hero']['headline'].startswith('Federal officials confirm')
    assert protected_material_update_pending_recomposition(result['hero'])


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


def test_model_response_parser_skips_thinking_blocks():
    from plain_engine.model_response import extract_model_text
    response = SimpleNamespace(content=[
        SimpleNamespace(type='thinking', thinking='private reasoning'),
        SimpleNamespace(type='text', text='  {"ok": true}  '),
    ])
    assert extract_model_text(response) == '{"ok": true}'


def test_assignment_pipeline_accepts_thinking_block_before_assignment_json():
    class ThinkingMessages:
        def __init__(self):
            self.calls = 0
            self.prompts = []
        def create(self, **kwargs):
            self.prompts.append(kwargs)
            self.calls += 1
            if self.calls == 1:
                payload = json.dumps({'hero': {'source_index': 1, 'angle': 'national policy', 'urgency_score': 8}, 'cards': []})
            else:
                payload = json.dumps({'headline': 'Federal agency announces national policy action', 'body': _long_body()})
            return SimpleNamespace(content=[
                SimpleNamespace(type='thinking', thinking='internal'),
                SimpleNamespace(type='text', text=payload),
            ])
    class ThinkingClient:
        def __init__(self): self.messages = ThinkingMessages()
        def with_options(self, **kwargs): return self
    source_text = 'Federal officials announced a national policy action Friday in Washington. ' + words('detail', 220)
    source = {
        'title': 'Federal agency announces national policy action',
        'summary': source_text[:700], 'article_text': source_text,
        'link': 'https://example.com/policy', 'source_quality': 'full',
        'published': 'Fri, 04 Sep 2026 16:00:00 GMT', 'publisher_name': 'Reuters',
    }
    client = ThinkingClient()
    result = run_live_assignment_category(client, 'us', 'U.S.', [source], card_count=0)
    assert result['hero']['source_index'] == 1
    assert result['hero']['headline'] == 'Federal agency announces national policy action'
    # Sonnet 5 enables adaptive thinking by default. The assignment editor is a
    # bounded JSON selection task, so production must disable thinking explicitly.
    assert client.messages.prompts[0]['model'] == 'claude-sonnet-5'
    assert client.messages.prompts[0]['thinking'] == {'type': 'disabled'}
    assert client.messages.prompts[0]['max_tokens'] == 1800


def test_model_response_parser_raises_clear_error_when_no_text_block():
    import pytest
    from plain_engine.model_response import extract_model_text
    response = SimpleNamespace(
        content=[SimpleNamespace(type='thinking', thinking='internal')],
        stop_reason='max_tokens',
        usage=SimpleNamespace(output_tokens=1800),
    )
    with pytest.raises(ValueError, match=r"no text blocks .*stop_reason='max_tokens'.*thinking.*1800"):
        extract_model_text(response)


def test_write_barrier_recomposes_repairable_material_update_before_public_render(monkeypatch,tmp_path):
    import scripts.generate as gen
    class ComposerMessages:
        def create(self,**kwargs):
            body=(
                'Federal officials said Friday that the national investigation opened earlier this year now includes a newly confirmed enforcement action, a development the agency announced after its latest review. The new enforcement action changes the status of the existing investigation and applies nationwide.\n\n'
                + 'The agency said the investigation began earlier this year after officials identified concerns involving the same program and institutions. The newly confirmed action follows that investigation and adds a formal enforcement step to the case. ' * 6
                + '\n\nOfficials said the enforcement action is now in effect and provided additional details about how it applies to the institutions covered by the investigation. The agency did not describe any broader consequences beyond the action contained in the supplied reports. ' * 5
            )
            return SimpleNamespace(content=[SimpleNamespace(type='text',text=json.dumps({
                'headline':'Federal agency adds enforcement action to national investigation',
                'teaser':'The agency added a newly confirmed enforcement action to an investigation opened earlier this year, advancing the same national case.',
                'body':body,
            }))])
    class ComposerClient:
        def __init__(self): self.messages=ComposerMessages()
        def with_options(self,**kwargs): return self
    class NoopCache:
        def get(self,*args,**kwargs): return CACHE_MISS
        def put(self,*args,**kwargs): return None

    monkeypatch.setattr(gen,'OUTPUT_DIR',tmp_path)
    (tmp_path/'articles').mkdir()
    archive=[{
        'slug':'2026-09-03-existing','headline':'Federal agency opens national investigation',
        'teaser':'Federal officials opened a national investigation earlier this year.',
        'body':'Federal officials opened a national investigation earlier this year involving the same program and institutions. '+('The agency described the original investigation and its scope. '*30),
        'category_key':'us','category_label':'U.S.','date':'2026-09-03','source_url':'https://old.example/a',
    }]
    (tmp_path/'archive.json').write_text(json.dumps(archive),encoding='utf-8')
    hero={
        'headline':'Federal agency confirms new enforcement action','body':'Too short.','teaser':'New action.',
        'article_text':'Federal officials confirmed Friday a new enforcement action in the national investigation opened earlier this year. '+('The agency described the enforcement action and the investigation. '*50),
        'source_title':'Federal agency confirms new enforcement action','link':'https://new.example/u',
        'source_published_raw':'Fri, 04 Sep 2026 16:00:00 GMT','pre_generation_material_update':True,
        'pre_generation_material_update_canonical_slug':'2026-09-03-existing',
        'semantic_material_update_decision':{'action':'update_existing_canonical','same_real_world_event':True,'material_new_update':True,'selected_candidate_slug':'2026-09-03-existing','novel_facts':['new enforcement action']},
        'publication_quality_ok':False,'publication_quality_reasons':['body_under_120_words'],
        '_force_material_update_recomposition':True,
    }
    category={'category_key':'us','category_label':'U.S.','hero':hero,'cards':[]}
    result=gen.write_archives([category],category,client=ComposerClient(),cache=NoopCache(),material_update_entries=[])
    assert result[0]['headline']=='Federal agency adds enforcement action to national investigation'
    assert hero['publication_quality_ok'] is True
    assert hero['material_update_recomposition_completed'] is True
    assert '_force_material_update_recomposition' not in hero


def test_parse_first_json_value_tolerates_trailing_model_commentary():
    from plain_engine.model_response import parse_first_json_value
    raw='[2, 1, 4]\n\nI ranked these by importance.'
    assert parse_first_json_value(raw, expected_type=list)==[2,1,4]


def test_plain_category_classification_keeps_non_none_pool_depth():
    import scripts.generate as gen
    rows=[];mapping={}
    for i in range(12):
        row={'title':f'Business source {i}','link':f'https://example.com/{i}','source_quality':'full'}
        rows.append(row)
        key=(row['link'],f'business source {i}')
        if i < 3:
            mapping[key]=['business']
        elif i == 11:
            mapping[key]=['none']
        else:
            mapping[key]=['us']
    kept=gen._apply_category_classification(rows,'business',mapping,limit=18)
    assert len(kept)==11
    assert [r['category_fit_hint'] for r in kept[:3]]==['positive']*3
    assert all(r['category_fit_hint']=='cross_tagged' for r in kept[3:])
    assert not any(r['title']=='Business source 11' for r in kept)


def test_assignment_pipeline_backfills_to_section_depth_without_weakening_quality(monkeypatch):
    import plain_engine.assignment_pipeline as ap
    now='Sat, 05 Sep 2026 18:00:00 GMT'
    sources=[]
    for i in range(12):
        sources.append({
            'title':['Chipmaker unveils new processor architecture','Space telescope detects distant exoplanet atmosphere','Cybersecurity firm patches enterprise flaw','Battery startup opens advanced manufacturing plant','Researchers publish quantum networking breakthrough','Cloud provider launches new database service','Robotics company demonstrates warehouse automation','Scientists map deep ocean microbial ecosystem','Semiconductor consortium sets packaging standard','Satellite operator expands broadband constellation','University team develops flexible solar material','AI lab releases open evaluation benchmark'][i],
            'summary':('Confirmed reporting about technology development number %d. ' % i)+words('detail',120),
            'article_text':('Confirmed reporting about technology development number %d. ' % i)+words('detail',180),
            'link':f'https://example.com/tech/{i}',
            'source_quality':'full','published':now,'publisher_name':'Example',
        })
    def fake_editor(client,category_key,category_label,sources,*,card_count,timeout_seconds):
        return {
            'hero':{'source_index':1,'angle':'lead','urgency_score':8},
            'cards':[{'source_index':2,'angle':'card one','urgency_score':7},{'source_index':3,'angle':'card two','urgency_score':6}],
            'rejected':[],
        }
    def fake_writer(client,*,category_key,category_label,source,assignment,hero,timeout_seconds):
        idx=int(assignment['source_index'])
        return {
            'headline':source['title'],
            'body':_long_body(),
            'teaser':'A concise confirmed update for this story.',
            'source_index':idx,
            'published':source['published'],'source_published_raw':source['published'],
            'link':source['link'],'source_title':source['title'],'source_summary':source['summary'],
            'article_text':source['article_text'],'source_quality':'full','source_name':'Example',
            'publication_quality_ok':True,'publication_quality_reasons':[],
        }
    monkeypatch.setattr(ap,'run_assignment_editor',fake_editor)
    monkeypatch.setattr(ap,'run_assignment_writer',fake_writer)
    result=ap.run_live_assignment_category(object(),'tech','Tech & Science',sources,card_count=8,timeout_seconds=240)
    assert len(result['cards'])==8
    diag=result['assignment_editor']
    assert diag['editor_selected_card_count']==2
    assert diag['section_depth_accepted_cards']==8
    assert diag['section_depth_shortfall']==0
    assert diag['depth_backfill_writer_attempts']==6


def test_assignment_pipeline_replaces_failed_card_with_next_safe_source(monkeypatch):
    import plain_engine.assignment_pipeline as ap
    now='Sat, 05 Sep 2026 18:00:00 GMT'
    sources=[]
    for i in range(7):
        sources.append({
            'title':['Mars orbiter returns new mineral survey','Biotech researchers report vaccine trial results','Astronomers observe unusual stellar explosion','Energy lab improves fusion magnet performance','Ocean researchers document coral recovery','Materials team creates heat resistant coating','Weather satellite begins new forecasting mission'][i],
            'summary':('Confirmed reporting about science development number %d. ' % i)+words('detail',120),
            'article_text':('Confirmed reporting about science development number %d. ' % i)+words('detail',180),
            'link':f'https://example.com/science/{i}',
            'source_quality':'full','published':now,'publisher_name':'Example',
        })
    def fake_editor(client,category_key,category_label,sources,*,card_count,timeout_seconds):
        return {'hero':{'source_index':1,'angle':'lead','urgency_score':8},'cards':[{'source_index':2,'angle':'bad card','urgency_score':7},{'source_index':3,'angle':'good card','urgency_score':6}], 'rejected':[]}
    def fake_writer(client,*,category_key,category_label,source,assignment,hero,timeout_seconds):
        idx=int(assignment['source_index'])
        ok = hero or idx != 2
        return {
            'headline':source['title'],'body':_long_body() if ok else 'too short',
            'teaser':'A concise confirmed update.','source_index':idx,'published':source['published'],
            'source_published_raw':source['published'],'link':source['link'],'source_title':source['title'],
            'source_summary':source['summary'],'article_text':source['article_text'],'source_quality':'full',
            'source_name':'Example','publication_quality_ok':ok,
            'publication_quality_reasons':[] if ok else ['body_under_90_words'],
        }
    monkeypatch.setattr(ap,'run_assignment_editor',fake_editor)
    monkeypatch.setattr(ap,'run_assignment_writer',fake_writer)
    result=ap.run_live_assignment_category(object(),'tech','Tech & Science',sources,card_count=4,timeout_seconds=240)
    assert len(result['cards'])==4
    assert 2 not in [c['source_index'] for c in result['cards']]
    assert len(result['assignment_editor']['writer_failures'])==1
    assert result['assignment_editor']['section_depth_shortfall']==0


def test_source_depth_gate_rejects_thin_before_materiality_and_writer():
    import scripts.generate as gen
    ready = {'title':'Full source','source_quality':'full','article_text':words('fact',100)}
    summary = {'title':'Summary source','source_quality':'summary','summary':words('fact',85)}
    brief = {'title':'Brief source','source_quality':'brief','summary':words('fact',70)}
    deceptive = {'title':'Short full source','source_quality':'full','article_text':words('fact',50)}
    sets={'business':[ready,summary,brief,deceptive]}
    report=gen._filter_publication_ready_sources(sets)
    assert sets['business']==[ready,summary]
    assert report['business']['publishable']==2
    assert report['business']['rejected']==2


def test_pre_generation_materiality_never_grants_update_authority_to_thin_source(monkeypatch,tmp_path):
    import scripts.generate as gen
    monkeypatch.setattr(gen,'OUTPUT_DIR',tmp_path)
    calls=[]
    monkeypatch.setattr(gen,'_pre_generation_semantic_decision',lambda *a,**k: calls.append(True) or {'action':gen.ACTION_UPDATE,'selected_candidate_slug':'old','same_real_world_event':True,'material_new_update':True})
    source={'title':'Thin update','link':'https://example.com/thin','source_quality':'brief','summary':words('tiny',60)}
    source_sets={'politics':[source]}
    archive=[{'slug':'old','headline':'Old story','body':words('old',160)}]
    report=gen.apply_pre_generation_materiality(source_sets,archive,object(),object())
    assert calls==[]
    assert not source.get('pre_generation_material_update')
    assert report.get('weak_source_skipped')==1


def test_archive_depth_backfill_preserves_live_copy_and_fills_supporting_slots(monkeypatch,tmp_path):
    import scripts.generate as gen
    monkeypatch.setattr(gen,'OUTPUT_DIR',tmp_path)
    (tmp_path/'articles').mkdir()
    live_hero={'headline':'Fresh hero','body':words('fresh',150),'story_id':'fresh-hero'}
    live_card={'headline':'Fresh card','body':words('fresh',110),'story_id':'fresh-card'}
    category={'category_key':'business','category_label':'Business','hero':live_hero,'cards':[live_card]}
    archive=[]
    for i in range(12):
        slug=f'2026-09-0{max(1,5-(i//5))}-archive-{i}'
        body=words(f'archive{i}_',120)
        archive.append({'slug':slug,'headline':f'Archive business story {i}','body':body,'teaser':'Archived reporting','category_key':'business','category_label':'Business','date':'2026-09-05','lastmod':'2026-09-05','story_id':f'archive-{i}','event_key':f'event-{i}'})
        (tmp_path/'articles'/f'{slug}.html').write_text('<div class="article-body"><p>'+body+'</p></div>',encoding='utf-8')
    added=gen._archive_depth_backfill([category],archive,target_cards=8,max_age_days=7)
    assert added==7
    assert category['hero'] is live_hero
    assert category['cards'][0] is live_card
    assert len(category['cards'])==8
    assert all(c.get('_archive_depth_backfill') for c in category['cards'][1:])


def test_plain_card_quality_contract_is_summary_shaped_not_article_shaped():
    from plain_engine.article_quality import publication_quality, word_count
    p1 = 'Federal officials announced the change Friday, saying the new rule applies nationwide and takes effect next month for agencies covered by the program. The department said the change is intended to standardize how offices report the same information across the country.'
    p2 = 'The revision changes reporting requirements and gives participating offices updated deadlines for submitting required records and notices. Officials said agencies will receive implementation guidance before the rule takes effect, including instructions for the first reporting cycle.'
    item = {
        'headline':'Federal agency changes nationwide reporting rule',
        'body':p1+'\n\n'+p2,
        'article_text':('Federal agency changes nationwide reporting rule. '+('Confirmed source detail about the reporting rule and implementation. '*30)),
        'source_title':'Federal agency changes nationwide reporting rule',
    }
    assert 60 <= word_count(item['body']) <= 140
    ok,reasons = publication_quality(item,hero=False)
    assert ok, reasons


def test_plain_card_quality_rejects_full_length_article_body():
    from plain_engine.article_quality import publication_quality
    body = ('Federal officials announced the change Friday and described the new reporting rule in detail. '*12) + '\n\n' + ('The department also outlined implementation requirements for participating offices nationwide. '*12)
    item = {
        'headline':'Federal agency changes nationwide reporting rule',
        'body':body,
        'article_text':body,
        'source_title':'Federal agency changes nationwide reporting rule',
    }
    ok,reasons = publication_quality(item,hero=False)
    assert not ok
    assert 'card_body_over_140_words' in reasons


def test_archive_depth_backfill_never_exposes_full_canonical_article_as_card(monkeypatch,tmp_path):
    import scripts.generate as gen
    from plain_engine.article_quality import word_count, paragraph_count
    monkeypatch.setattr(gen,'OUTPUT_DIR',tmp_path)
    (tmp_path/'articles').mkdir()
    live_hero={'headline':'Fresh hero','body':gen._compact_card_summary(('Fresh hero detail. '*100), 'Fresh hero teaser') + '\n\n' + ('hero full article detail '*100), 'story_id':'fresh-hero'}
    category={'category_key':'business','category_label':'Business','hero':live_hero,'cards':[]}
    full_body = (
        'A major company announced a nationwide expansion Friday after reporting stronger demand across several markets. Executives said the expansion will begin this fall and include new operations in multiple states. '
        'The company said the plan follows a year of investment in logistics and staffing. It expects the first locations to open before the end of the year.\n\n'
        + ('Additional full-length canonical article detail about financing, staffing, facilities and the company history. '*45)
    )
    archive=[{
        'slug':'2026-09-05-archive-business','headline':'Company announces nationwide expansion',
        'body':full_body,'teaser':'The company plans a nationwide expansion beginning this fall after reporting stronger demand.',
        'category_key':'business','category_label':'Business','date':'2026-09-05','lastmod':'2026-09-05',
        'story_id':'archive-business','event_key':'event-business'
    }]
    (tmp_path/'articles'/'2026-09-05-archive-business.html').write_text('<div class="article-body"><p>'+full_body.replace('\n\n','</p><p>')+'</p></div>',encoding='utf-8')
    added=gen._archive_depth_backfill([category],archive,target_cards=1,max_age_days=7)
    assert added==1
    card=category['cards'][0]
    assert card['body'] != full_body
    assert word_count(card['body']) <= gen.CARD_SUMMARY_MAX_WORDS
    assert paragraph_count(card['body']) == 2
    assert card.get('_plain_card_summary') is True


def test_final_plain_card_surface_contract_compacts_any_accidental_full_body():
    import scripts.generate as gen
    from plain_engine.article_quality import word_count, paragraph_count
    full = ('The lead contains the most important confirmed facts about the story and establishes what happened. '*6) + '\n\n' + ('The rest of this text is a full article that should never be exposed through a supporting card. '*30)
    category={'category_key':'world','category_label':'World','hero':{'headline':'Hero','body':full},'cards':[{'headline':'Supporting story','teaser':'Officials confirmed the central development Friday in a statement describing the immediate change.','body':full}]}
    changed=gen._enforce_card_summary_product([category])
    assert changed==1
    assert word_count(category['cards'][0]['body']) <= gen.CARD_SUMMARY_MAX_WORDS
    assert paragraph_count(category['cards'][0]['body']) == 2
    assert category['hero']['body'] == full
