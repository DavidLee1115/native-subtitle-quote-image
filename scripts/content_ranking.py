#!/usr/bin/env python3
"""Evidence-bound ranking of agent-assessed complete-transcript candidates."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

DIMENSIONS = ('hook', 'standalone', 'conflict', 'emotion', 'novelty', 'specificity', 'shareability', 'account_fit')
PROFILES = {
    'cold': dict(zip(DIMENSIONS + ('visual_viability',), (.18,.16,.15,.06,.15,.07,.07,.06,.10))),
    'growth': dict(zip(DIMENSIONS + ('visual_viability',), (.13,.13,.10,.08,.10,.10,.12,.14,.10))),
    'mature': dict(zip(DIMENSIONS + ('visual_viability',), (.08,.12,.07,.08,.08,.13,.12,.22,.10))),
}
CONFIG = {'version':'phase4a-1','profiles':PROFILES,'score_range':[0,5],
          'candidate_count':[20,30], 'final_count':5, 'near_quality':.15,
          'redundancy_penalty':.65,'lexical_reject_threshold':.80,
          'evidence_integrity':'hard_gate','visual_unknown':'fail_closed',
          'cluster_policy':'unique','quote_policy':'whole_contiguous_cues'}
def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
CONFIG_SHA256 = digest(CONFIG)
def norm(text):
    return ' '.join(text.split())
def tokens(text):
    return set(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]',text.lower()))
def similarity(a,b):
    x,y=tokens(a),tokens(b)
    return len(x&y)/max(1,len(x|y))
def valid_score(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and 0<=value<=5

def rank(transcript,assessment,visual,profile='cold'):
    """Return audited pool and selection. Structural input errors abort before writing."""
    if profile not in PROFILES: raise ValueError('unknown account profile')
    cues=transcript['cues']
    if not cues: raise ValueError('empty transcript')
    ids=[str(c['id']) for c in cues]
    if len(set(ids))!=len(ids): raise ValueError('duplicate transcript cue ids')
    for i,c in enumerate(cues):
        if not isinstance(c['text'],str) or not c['text'].strip() or not all(isinstance(c[k],(int,float)) and math.isfinite(c[k]) for k in ('start','end')) or c['start']<0 or c['end']<=c['start']:
            raise ValueError('invalid transcript cue')
        if i and c['start']<cues[i-1]['start']: raise ValueError('unordered transcript')
    if assessment.get('video_id')!=transcript.get('video_id'): raise ValueError('video id mismatch')
    if assessment.get('full_transcript_reviewed') is not True or assessment.get('transcript_sha256')!=digest(transcript):
        raise ValueError('assessment must bind to fully reviewed transcript hash')
    candidates=assessment['candidates']
    if not 20<=len(candidates)<=30: raise ValueError('candidate pool must contain 20 to 30 candidates')
    candidate_ids=[c['id'] for c in candidates]
    if len(set(candidate_ids))!=len(candidate_ids): raise ValueError('duplicate candidate ids')
    visuals=visual['candidates']
    if len({v['candidate_id'] for v in visuals})!=len(visuals): raise ValueError('duplicate visual candidate ids')
    vmap={v['candidate_id']:v for v in visuals}
    if set(vmap)!=set(candidate_ids): raise ValueError('visual assessment must cover exactly the entire pool before selection')
    pairs={}
    for p in assessment.get('redundancy_pairs',[]):
        a,b=p['candidate_ids']; s=p['similarity']
        if a not in candidate_ids or b not in candidate_ids or a==b or not isinstance(s,(float,int)) or not 0<=s<=1 or not p.get('reason'):
            raise ValueError('invalid semantic redundancy pair')
        pairs[frozenset((a,b))]=p
    weights=PROFILES[profile]; pool=[]
    for c in candidates:
        reasons=[]; scores={}
        for d in DIMENSIONS:
            value=c.get('scores',{}).get(d,{})
            if not valid_score(value.get('score')) or not isinstance(value.get('reason'),str) or not value['reason'].strip():
                raise ValueError(f'{c["id"]}: invalid {d} score/reason')
            scores[d]=value
        for field in ('topic','cluster','angle'):
            if not isinstance(c.get(field),str) or not c[field].strip(): raise ValueError(f'missing {field}')
        qids=[str(x) for x in c.get('quote_cue_ids',[])]
        positions=[ids.index(x) for x in qids if x in ids]
        bound=[]
        if not qids or len(positions)!=len(qids) or positions!=list(range(positions[0],positions[0]+len(positions))):
            reasons.append('quote cues missing, duplicated, non-contiguous, or out of order')
        else:
            bound=[cues[i] for i in positions]
            if norm(c.get('quote',''))!=norm(' '.join(x['text'] for x in bound)):
                reasons.append('quote text differs from source transcript')
        window=c.get('semantic_window',{})
        window_ok=all(isinstance(window.get(k),(int,float)) and math.isfinite(window[k]) for k in ('start','end'))
        if not window_ok or window['start']<0 or window['end']>max(x['end'] for x in cues) or window['start']>=window['end'] or (bound and (window['start']>bound[0]['start'] or window['end']<bound[-1]['end'])):
            reasons.append('semantic window invalid or does not contain quote')
        integrity=c.get('context_integrity',{})
        if integrity.get('passed') is not True or not isinstance(integrity.get('reason'),str) or not integrity['reason'].strip():
            reasons.append('context integrity review missing or failed')
        scores['evidence_integrity']={'score':5 if not reasons else 0,'reason':'; '.join(reasons) if reasons else 'Exact contiguous source cues verified; '+integrity['reason'], 'hard_constraint':True}
        v=vmap[c['id']]
        visual_reasons=[]
        if v.get('semantic_window')!=window: visual_reasons.append('visual semantic window mismatch')
        if v.get('status') not in ('viable','quote_first'): visual_reasons.append('visual status unknown or unusable')
        if not valid_score(v.get('score')) or not isinstance(v.get('reason'),str) or not v.get('reason','').strip() or not isinstance(v.get('evidence'),list) or not v.get('evidence'):
            visual_reasons.append('visual score or evidence missing')
        scores['visual_viability']={'score':v['score'] if valid_score(v.get('score')) else 0,'reason':v.get('reason','Missing visual signal'),'status':v.get('status','unknown')}
        reasons.extend(visual_reasons)
        score=sum(weights[d]*scores[d]['score'] for d in weights)
        semantic=sum(weights[d]*scores[d]['score'] for d in DIMENSIONS)/(1-weights['visual_viability'])
        pool.append({**c,'scores':scores,'source_transcript_evidence':bound,'source_transcript_sha256':digest(transcript),
                     'semantic_window_evidence':[x for x in cues if window_ok and x['end']>window['start'] and x['start']<window['end']],
                     'visual_signal':v,'eligible':not reasons,'rejection_reasons':reasons,
                     'weighted_score':round(score,6),'semantic_score':round(semantic,6)})
    pool.sort(key=lambda c:(-c['weighted_score'],c['id']))
    for i,c in enumerate(pool,1): c['base_rank']=i
    selected=[]; remaining=[c for c in pool if c['eligible']]
    decisions=[]; selection_rounds=[]
    def redundancy(a,b):
        lexical=similarity(a['quote'],b['quote'])
        pair=pairs.get(frozenset((a['id'],b['id'])))
        return max(lexical,pair['similarity'] if pair else 0),lexical,pair
    while remaining and len(selected)<5:
        choices=[]
        for c in remaining:
            red=max((redundancy(c,s)[0] for s in selected),default=0)
            c['redundancy_penalty']=round(CONFIG['redundancy_penalty']*red,6)
            c['adjusted_score']=round(c['weighted_score']-c['redundancy_penalty'],6)
            choices.append(c)
        best=max(c['adjusted_score'] for c in choices)
        near=[c for c in choices if c['adjusted_score']>=best-CONFIG['near_quality']]
        chosen=sorted(near,key=lambda c:(-c['scores']['visual_viability']['score'],c['visual_signal']['status']!='viable',-c['adjusted_score'],c['id']))[0]
        selection_rounds.append({
            'round':len(selected)+1,'best_adjusted_score':best,
            'near_quality_threshold':best-CONFIG['near_quality'],
            'near_best_candidate_ids':[c['id'] for c in near],
            'chosen':{'candidate_id':chosen['id'],'adjusted_score':chosen['adjusted_score'],
                      'visual_score':chosen['scores']['visual_viability']['score'],
                      'visual_status':chosen['visual_signal']['status']},
            'candidates':[{'candidate_id':c['id'],'adjusted_score':c['adjusted_score'],
                           'redundancy_penalty':c['redundancy_penalty'],
                           'visual_score':c['scores']['visual_viability']['score'],
                           'visual_status':c['visual_signal']['status']} for c in choices]})
        chosen['selection_rank']=len(selected)+1
        chosen['selection_reason']=f"Distinct cluster {chosen['cluster']}; angle: {chosen['angle']}. Adjusted score {chosen['adjusted_score']:.3f}; visual {chosen['scores']['visual_viability']['score']}/5 ({chosen['visual_signal']['status']}). Among candidates within {CONFIG['near_quality']} points, prefer stronger visual viability. Evidence gate passed."
        selected.append(chosen); remaining.remove(chosen)
        for other in remaining[:]:
            red,lexical,pair=redundancy(chosen,other)
            if other['cluster']==chosen['cluster'] or red>=CONFIG['lexical_reject_threshold']:
                why='same semantic cluster' if other['cluster']==chosen['cluster'] else 'semantic/lexical redundancy exceeds threshold'
                other['elimination_reason']=f"{why}; overlaps selected {chosen['id']}"
                remaining.remove(other)
                decisions.append({'candidate_id':other['id'],'selected_id':chosen['id'],'reason':why,'similarity':round(red,6),'lexical_similarity':round(lexical,6),'semantic_evidence':pair})
    selected_ids={c['id'] for c in selected}
    rejected=[]
    for c in pool:
        if c['id'] not in selected_ids:
            if 'elimination_reason' not in c:
                if not c['eligible']:
                    c['elimination_reason']='; '.join(c['rejection_reasons'])
                else:
                    last=selection_rounds[-1]; winner=last['chosen']
                    if c['id'] not in last['near_best_candidate_ids']:
                        cause=f"below near-quality cutoff {last['near_quality_threshold']:.6f}"
                    else:
                        cause='within near-quality set; visual score, viable status, adjusted score, then id tie-break favored winner'
                    c['elimination_reason']=(f"Final round {last['round']}: adjusted score {c['adjusted_score']:.6f}, "
                        f"redundancy penalty {c['redundancy_penalty']:.6f}, visual {c['scores']['visual_viability']['score']}/5 "
                        f"({c['visual_signal']['status']}); {cause}. Winner {winner['candidate_id']}: "
                        f"adjusted score {winner['adjusted_score']:.6f}, visual {winner['visual_score']}/5 ({winner['visual_status']}).")
            rejected.append({'id':c['id'],'base_rank':c['base_rank'],'weighted_score':c['weighted_score'],'reason':c['elimination_reason']})
    comparison=[{'a':a['id'],'b':b['id'],'similarity':round(redundancy(a,b)[0],6),'different_cluster':a['cluster']!=b['cluster'],'angles':[a['angle'],b['angle']]} for i,a in enumerate(selected) for b in selected[i+1:]]
    shared={'video_id':transcript['video_id'],'profile':profile,'configuration':CONFIG,'configuration_sha256':CONFIG_SHA256,'transcript_sha256':digest(transcript),'visual_pool_read_count':len(visuals),'assessment_sha256':digest(assessment),'visual_sha256':digest(visual),
            'assessment_metadata':{k:v for k,v in assessment.items() if k not in ('candidates','redundancy_pairs')},
            'scoring_evidence_type':'model_judgment_not_audience_measurement'}
    selection={**shared,'status':'complete' if len(selected)==5 else 'insufficient_eligible_diverse_candidates','selected':selected,
               'selection_rounds':selection_rounds,'rejected_candidates':rejected,'high_scoring_rejected_candidates':[r for r in rejected if r['base_rank']<=10],
               'diversity':{'passed':len(selected)==5 and len({c['cluster'] for c in selected})==5 and all(p['similarity']<.8 for p in comparison),'unique_clusters':len({c['cluster'] for c in selected}),'pairwise':comparison,'elimination_decisions':decisions}}
    return {**shared,'candidates':pool},selection

def scoring_table(pool,selection):
    dims=DIMENSIONS+('visual_viability','evidence_integrity')
    lines=['# Candidate scoring','',f"Profile: {pool['profile']}; frozen configuration: `{CONFIG_SHA256}`",'', '| Candidate | Topic | '+' | '.join(dims)+' | Weighted | Outcome |','|'+' --- |'*(len(dims)+4)]
    selected={c['id'] for c in selection['selected']}
    clean=lambda x:str(x).replace('|','/').replace('\n',' ')
    for c in pool['candidates']:
        cells=[c['id'],c['topic']]+[f"{c['scores'][d]['score']}: {c['scores'][d]['reason']}" for d in dims]+[c['weighted_score'],'selected' if c['id'] in selected else c['elimination_reason']]
        lines.append('| '+' | '.join(clean(x) for x in cells)+' |')
    lines+=['','## Final selection','']+[f"{c['selection_rank']}. {c['topic']}: {c['selection_reason']}" for c in selection['selected']]
    lines+=['','## Semantic diversity','',json.dumps(selection['diversity'],ensure_ascii=False,indent=2)]
    return '\n'.join(lines)+'\n'
def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('transcript','assessment','visual','output'): p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--profile',choices=PROFILES,default='cold'); args=p.parse_args()
    read=lambda path:json.loads(path.read_text())
    pool,selection=rank(read(args.transcript),read(args.assessment),read(args.visual),args.profile)
    args.output.mkdir(parents=True,exist_ok=True)
    for name,value in [('ranking-candidates.json',pool),('final-selection.json',selection)]:
        (args.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    (args.output/'scoring-table.md').write_text(scoring_table(pool,selection))
    print(json.dumps({'status':selection['status'],'selected':[c['id'] for c in selection['selected']],'configuration_sha256':CONFIG_SHA256}))
if __name__=='__main__': main()
