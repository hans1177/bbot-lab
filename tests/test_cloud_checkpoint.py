# 파일명: tests/test_cloud_checkpoint.py
import contextlib,hashlib,io,json,os,shutil,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import yaml

REPO=Path(__file__).resolve().parents[1]
WORKFLOW=yaml.safe_load((REPO/'.github/workflows/research-run.yml').read_text())
STEPS={s.get('name'):s for s in WORKFLOW['jobs']['research']['steps'] if s.get('name')}

class CloudCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'runtime';self.root.mkdir()
        self.state=self.root/'strategy_lab/results/v3';self.state.mkdir(parents=True)
        (self.root/'ROADMAP_CONSTITUTION_V3.json').write_text('{"policy":"fixture"}')
        (self.root/'strategy_lab/run_strategy.py').write_text('# fixed fixture engine\n')
        (self.root/'INPUT_RECEIPT.json').write_text('{"start.zip":"fixed","extra.zip":"fixed"}')
        (self.state/'trial_ledger.json').write_text('{"schema":"fixture","entries":[{"trial":1}]}')
        (self.state/'candidate.py').write_text('# exact candidate fixture\n')
        self.env={'BBOT_LOCAL_ROOT':str(self.root),'RUNNER_TEMP':str(self.base),'GITHUB_SHA':'fixture','GITHUB_RUN_ID':'1'}
    def run_step(self,name):
        with patch.dict(os.environ,self.env),contextlib.redirect_stdout(io.StringIO()):
            exec(compile(STEPS[name]['run'],name,'exec'),{})
    def save(self):
        self.run_step('Preserve research checkpoint')
        shutil.copytree(self.base/'research-checkpoint-out',self.base/'research-checkpoint-in')
    def test_state_roundtrip_and_code_preserved(self):
        before={p.relative_to(self.state):p.read_bytes() for p in self.state.rglob('*') if p.is_file()}
        self.save();(self.state/'trial_ledger.json').write_text('{}')
        self.run_step('Restore exact research state')
        self.assertEqual(before,{p.relative_to(self.state):p.read_bytes() for p in self.state.rglob('*') if p.is_file()})
        self.assertEqual((self.root/'strategy_lab/run_strategy.py').read_text(),'# fixed fixture engine\n')
    def test_archive_tamper_rejected(self):
        self.save();(self.base/'research-checkpoint-in/checkpoint.zip').write_bytes(b'tamper')
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_HASH_MISMATCH'):self.run_step('Restore exact research state')
    def test_changed_engine_rejected(self):
        self.save();(self.root/'strategy_lab/run_strategy.py').write_text('# changed engine')
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_ENGINE_MISMATCH'):self.run_step('Restore exact research state')
    def test_changed_inputs_rejected(self):
        self.save();(self.root/'INPUT_RECEIPT.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_INPUTS_CHANGED'):self.run_step('Restore exact research state')
    def test_30m_extension_preserves_other_research(self):
        old={'start.zip':'fixed','research_scope':['1H','6H','12H','1D','1W']}
        p=self.root/'INPUT_RECEIPT.json';p.write_text(json.dumps(old));self.save()
        new=dict(old,research_scope=['30M','1H','6H','12H','1D','1W'],official_30m={'period':['2019-09','2026-09'],'manifest_sha256':'fixture'})
        p.write_text(json.dumps(new));self.run_step('Restore exact research state')
        self.assertEqual(json.loads((self.state/'trial_ledger.json').read_text())['entries'],[{'trial':1}])
    def test_30m_extension_cannot_change_original_archives(self):
        old={'start.zip':'fixed','research_scope':['1H','6H','12H','1D','1W']}
        p=self.root/'INPUT_RECEIPT.json';p.write_text(json.dumps(old));self.save()
        new=dict(old,**{'start.zip':'changed','research_scope':['30M','1H','6H','12H','1D','1W'],'official_30m':{'period':['2019-09','2026-09'],'manifest_sha256':'fixture'}})
        p.write_text(json.dumps(new))
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_INPUTS_CHANGED'):self.run_step('Restore exact research state')
    def test_unlisted_archive_path_rejected(self):
        self.save();folder=self.base/'research-checkpoint-in';p=folder/'checkpoint.zip'
        with zipfile.ZipFile(p,'a') as z:z.writestr('../outside','bad')
        receipt=json.loads((folder/'CHECKPOINT.json').read_text());receipt['archive_sha256']=hashlib.sha256(p.read_bytes()).hexdigest();(folder/'CHECKPOINT.json').write_text(json.dumps(receipt))
        with self.assertRaisesRegex(RuntimeError,'CHECKPOINT_FILE_SET_MISMATCH'):self.run_step('Restore exact research state')
    def test_workflow_has_one_worker_and_no_checkout_credentials(self):
        self.assertFalse(WORKFLOW['concurrency']['cancel-in-progress'])
        job=WORKFLOW['jobs']['research'];self.assertLessEqual(job['timeout-minutes'],55)
        checkout=next(s for s in job['steps'] if str(s.get('uses','')).startswith('actions/checkout@'))
        self.assertFalse(checkout['with']['persist-credentials'])
        self.assertEqual(job['env']['B_BOT_V3_SERVER_PUSH_HOLD'],'1')
    def test_inline_python_compiles(self):
        for name,step in STEPS.items():
            if step.get('shell')=='python':compile(step['run'],name,'exec')
