import copy
import importlib.util
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('content_ranking',Path(__file__).resolve().parents[1]/'scripts/content_ranking.py')
r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)

def fixture():
    t={'video_id':'unseen','cues':[{'id':i,'start':i*10,'end':i*10+8,'text':f'unique{i} evidence{i} quote{i}'} for i in range(20)]}
    cs=[{'id':f'c{i}','quote_cue_ids':[i],'quote':c['text'],'semantic_window':{'start':c['start'],'end':c['end']},'topic':f'topic{i}','cluster':f'cluster{i}','angle':f'angle{i}', 'speaker':'Speaker','claim_status':'personal_view','scores':{d:{'score':4,'reason':'Model assessment'} for d in r.DIMENSIONS},'context_integrity':{'passed':True,'reason':'Context reviewed'}} for i,c in enumerate(t['cues'])]
    a={'video_id':'unseen','full_transcript_reviewed':True,'transcript_sha256':r.digest(t),'candidates':cs}
    v={'candidates':[{'candidate_id':c['id'],'semantic_window':c['semantic_window'],'status':'viable','score':4,'reason':'Read-only Phase 3 signal','evidence':['signal.json#window']} for c in cs]}
    return t,a,v

class RankingTest(unittest.TestCase):
    def test_profiles_and_repeatability(self):
        for p,w in r.PROFILES.items(): self.assertAlmostEqual(sum(w.values()),1)
        self.assertEqual(r.rank(*fixture()),r.rank(*fixture()))
        pool,result=r.rank(*fixture()); self.assertEqual(len(result['selected']),5)
        self.assertEqual(pool['visual_pool_read_count'],20)
        self.assertTrue(result['diversity']['passed'])
        self.assertEqual(result['selected'][0]['claim_status'],'personal_view')
    def test_forged_quote_cannot_win(self):
        t,a,v=fixture(); a['candidates'][0]['quote']='Fabricated more exciting statement'
        for d in r.DIMENSIONS: a['candidates'][0]['scores'][d]['score']=5
        pool,result=r.rank(t,a,v)
        bad=next(c for c in pool['candidates'] if c['id']=='c0')
        self.assertFalse(bad['eligible']); self.assertEqual(bad['scores']['evidence_integrity']['score'],0)
        self.assertNotIn('c0',[c['id'] for c in result['selected']])
    def test_noncontiguous_cues_and_window(self):
        for qids,window in [([0,2],{'start':0,'end':28}),([0],{'start':1,'end':8}),([0],{'start':0,'end':999}),([0,0],{'start':0,'end':8})]:
            t,a,v=fixture(); a['candidates'][0].update(quote_cue_ids=qids,semantic_window=window)
            pool,_=r.rank(t,a,v)
            self.assertFalse(next(c for c in pool['candidates'] if c['id']=='c0')['eligible'])
    def test_context_integrity_is_hard_constraint(self):
        t,a,v=fixture(); a['candidates'][0]['context_integrity']['passed']=False
        pool,_=r.rank(t,a,v)
        self.assertFalse(next(c for c in pool['candidates'] if c['id']=='c0')['eligible'])
    def test_visual_coverage_and_unknown(self):
        t,a,v=fixture(); v['candidates'].pop()
        with self.assertRaisesRegex(ValueError,'entire pool'): r.rank(t,a,v)
        t,a,v=fixture(); v['candidates'][0]['status']='unknown'
        _,result=r.rank(t,a,v); self.assertNotIn('c0',[c['id'] for c in result['selected']])
    def test_visual_quality_tie(self):
        t,a,v=fixture(); v['candidates'][19]['score']=5
        _,result=r.rank(t,a,v); self.assertEqual(result['selected'][0]['id'],'c19')
    def test_clusters_and_cross_cluster_redundancy(self):
        t,a,v=fixture(); a['candidates'][1]['cluster']='cluster0'
        a['redundancy_pairs']=[{'candidate_ids':['c0','c10'],'similarity':.95,'reason':'same argument despite different labels'}]
        _,result=r.rank(t,a,v); ids=[c['id'] for c in result['selected']]
        self.assertIn('c0',ids); self.assertNotIn('c1',ids); self.assertNotIn('c10',ids)
        self.assertEqual(len(result['diversity']['elimination_decisions']),2)
    def test_fail_closed_insufficient_diversity(self):
        t,a,v=fixture()
        for c in a['candidates']: c['cluster']='one'
        _,result=r.rank(t,a,v)
        self.assertEqual(len(result['selected']),1); self.assertFalse(result['diversity']['passed'])
        self.assertEqual(result['status'],'insufficient_eligible_diverse_candidates')
    def test_stale_full_transcript_assessment(self):
        t,a,v=fixture(); t['cues'][-1]['text']='source changed'
        with self.assertRaisesRegex(ValueError,'hash'): r.rank(t,a,v)
    def test_invalid_score(self):
        t,a,v=fixture(); a['candidates'][0]['scores']['hook']['score']=float('nan')
        with self.assertRaisesRegex(ValueError,'score'): r.rank(t,a,v)
if __name__=='__main__': unittest.main()
