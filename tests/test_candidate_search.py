import copy,hashlib,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
import numpy as np
import pandas as pd
LAB=Path(__file__).resolve().parents[1]/'strategy_lab'
sys.path.insert(0,str(LAB))
TEMP=tempfile.TemporaryDirectory(prefix='bbot-search-test-')
os.environ['BBOT_LOCAL_ROOT']=TEMP.name
import v3_hypothesis_factory as hf
import v3_memory_hypothesis_wave as mw
import v3_entry_hypothesis_wave_seal as seal
import v3_portable_strategy_packager as packager
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

def inputs(run_id='test'):
 n=900;rng=np.random.default_rng(21);close=100*np.exp(np.cumsum(rng.normal(.0001,.008,n)))
 frame=pd.DataFrame({'ts':int(pd.Timestamp('2018-01-01T00:00:00Z').timestamp())+np.arange(n)*1800,'open':close,'high':close*1.01,'low':close*.99,'close':close,'volume':np.ones(n)})
 contract={'schema':'B_BOT_CANDIDATE_SEARCH_V1','timeframe':'30M','run_id':run_id,'seed':11,'population_size':8,'generations':3,'optuna_trials':16,'max_evaluations':32,'max_seconds':60,'fee_rate':.0005,'training_start_utc':'2018-01-01T00:00:00Z','training_end_utc':'2018-02-01T00:00:00Z','oos_start_utc':'2019-01-01T00:00:00Z','sealed_start_utc':'2024-08-28T00:00:00Z'}
 return contract,{'BTCUSDT':frame}

class SearchTests(unittest.TestCase):
 def test_default_pool_preserved(self):
  rows=mw.candidate_pool('30M');self.assertEqual(len(rows),90);self.assertTrue(all(r['research_origin']=='MEMORY_DERIVED_BOUNDED_GRAMMAR' for r in rows));self.assertEqual(len(hf.research_recipes()),72)
 def test_ga_optuna_reproducible_and_bounded(self):
  c,frames=inputs('determinism');a=mw.candidate_pool('30M',c,frames);b=mw.candidate_pool('30M',c,frames)
  self.assertEqual(a,b);self.assertLessEqual(len(a),c['max_evaluations']);self.assertGreater(len(a),4);self.assertTrue(all(r['automatic_promotion'] is False for r in a))
 def test_future_and_bad_cost_rejected(self):
  c,frames=inputs('bad');bad=copy.deepcopy(frames);bad['BTCUSDT'].loc[len(bad['BTCUSDT'])-1,'ts']=pd.Timestamp('2025-01-01T00:00:00Z').timestamp()
  with self.assertRaisesRegex(ValueError,'NON_TRAINING_CANDLE'):mw.candidate_pool('30M',c,bad)
  c['fee_rate']=0
  with self.assertRaisesRegex(ValueError,'TRAINING_COST_REQUIRED'):mw.candidate_pool('30M',c,frames)
 def test_sealed_freeze_and_source_tamper(self):
  c,frames=inputs('frozen');manifest=mw.wave('30M',c,frames);self.assertFalse(manifest['provenance']['official_validation_executed']);self.assertEqual(seal.validate_wave_candidates(manifest['candidates']),[])
  self.assertEqual(mw.wave('30M',c,frames),manifest)
  changed=dict(c,seed=c['seed']+1)
  with self.assertRaisesRegex(RuntimeError,'SEALED_SEARCH_CONTRACT_MUTATION'):mw.wave('30M',changed,frames)
  source=hf.ROOT/manifest['candidates'][0]['canonical_source_file'];original=source.read_bytes();source.write_bytes(original+b'\n# changed')
  try:
   with self.assertRaisesRegex(RuntimeError,'SEALED_CANDIDATE_SOURCE_CHANGED'):mw.wave('30M',c,frames)
  finally:source.write_bytes(original)
 def test_portable_runtime_parity_closed_bar_prefix(self):
  c,frames=inputs();frame=frames['BTCUSDT']
  for mode in mw.MODES:
   for confirm in mw.CONFIRM_MODES:
    recipe={'mode':mode,'window':16,'threshold':.85,'confirm_mode':confirm}
    source,name=hf.module_for(recipe,'30M');self.assertNotIn('from v3_hypothesis_factory',source.read_text())
    artifact=hf.ROOT/'runtime'/source.name;packager.package_strategy(source,artifact)
    spec=importlib.util.spec_from_file_location(name,artifact);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    expected=hf.generate_signals_recipe(frame,recipe);np.testing.assert_array_equal(expected,module.generate_signals(frame,'30M'))
    for prefix in (80,170,450,899):
     np.testing.assert_array_equal(expected[:prefix],module.generate_signals(frame.iloc[:prefix],'30M'))
     self.assertEqual(hf.signal_recipe(frame.iloc[:prefix],recipe),module.signal(frame.iloc[:prefix]))
 def test_training_dataset_role_and_hash_required(self):
  c,frames=inputs('dataset');path=hf.ROOT/'data/candles/BTCUSDT-training.csv';path.parent.mkdir(parents=True,exist_ok=True);frames['BTCUSDT'].to_csv(path,index=False)
  row={'role':'OOS','timeframe':'30M','symbol':'BTCUSDT','path':str(path.relative_to(hf.ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
  c['training_files']=[row]
  with self.assertRaisesRegex(ValueError,'TRAINING_DATASET_ROLE_INVALID'):mw.wave('30M',c)
  row['role']='TRAINING_ONLY';row['sha256']='0'*64
  with self.assertRaisesRegex(RuntimeError,'TRAINING_DATASET_HASH_MISMATCH'):mw.wave('30M',c)
  row['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
  manifest=mw.wave('30M',c);self.assertFalse(manifest['provenance']['official_validation_executed'])
 def test_structural_diversity_caps(self):
  selected=mw._diversified_selection(mw.candidate_pool('30M'))
  from collections import Counter
  counts=Counter(mw._structural_group(row) for row in selected)
  self.assertLessEqual(len(selected),mw.WAVE_SIZE_PER_TF);self.assertLessEqual(len(counts),mw.STRUCTURAL_GROUP_LIMIT);self.assertTrue(all(n<=mw.MAX_PER_STRUCTURAL_GROUP for n in counts.values()))
 def test_frozen_source_not_overwritten(self):
  recipe={'mode':'TREND_Z','window':20,'threshold':.55,'confirm_mode':'BASE'}
  source,_=hf.module_for(recipe,'1H');source.write_text('# existing frozen code')
  with self.assertRaisesRegex(RuntimeError,'FROZEN_STRATEGY_SOURCE_CONFLICT'):hf.module_for(recipe,'1H')
  self.assertEqual(source.read_text(),'# existing frozen code')

def tearDownModule():
 TEMP.cleanup()

if __name__=='__main__':
 try:unittest.main(verbosity=2)
 finally:TEMP.cleanup()
