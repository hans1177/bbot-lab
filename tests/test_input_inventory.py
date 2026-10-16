# 파일명: tests/test_input_inventory.py
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class InputFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copy2(REPO / 'ROADMAP_CONSTITUTION_V3.json', self.root)

    def tearDown(self):
        self.temp.cleanup()

    def run_check(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        env = dict(os.environ, BBOT_LOCAL_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', str(REPO / 'strategy_lab/research_controller.py'), '--mode', 'managed-dry-run', '--check-inputs'], env=env, capture_output=True, text=True, timeout=20)
        self.assertIn(result.returncode, (0, 2), result.stderr)
        parsed = json.loads(result.stdout)
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(parsed['mutated'])
        self.assertFalse(parsed['orders_touched'])
        return result.returncode, parsed

    def fixture(self):
        # 파일 존재 검사 전용이며 실제 시세·수익성 검증 데이터가 아니다.
        values = {'gate_lab_fast.py': b'# engine fixture\n', 'gate_lab_exact_long_cache.parquet': b'cache fixture', 'strategy_lab/lab_config.json': b'{}', 'data/candles/BTCUSDT_30M.parquet': b'candle fixture'}
        source = b'# frozen source\n'
        values['strategy_lab/deploy_ready/candidate.py'] = source
        entry = {'strategy_id': 'FIXTURE', 'exact_strategy_version': 'EV_FIXTURE_30M', 'eligible': True, 'pipeline_stage': 'INDIVIDUAL_TRAILING_OPTIMIZATION_PENDING', 'canonical_source_file': 'strategy_lab/deploy_ready/candidate.py', 'source_sha256': hashlib.sha256(source).hexdigest()}
        states = {'historical_314_pipeline_stage2.json': {'entries': [entry]}, 'trial_ledger.json': {'schema': 'B_BOT_V3_TRIAL_LEDGER_V1', 'entries': []}, 'research_knowledge.json': {'records': {}}}
        for name, value in states.items():
            values['strategy_lab/results/v3/' + name] = json.dumps(value).encode()
        for name, raw in values.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return entry


class InputInventoryTests(InputFixture, unittest.TestCase):
    def test_missing_inputs_fail_without_state_writes(self):
        code, data = self.run_check()
        self.assertEqual(code, 2)
        self.assertFalse(data['ok'])
        self.assertIn('gate_lab_fast.py', data['input_inventory']['missing'])

    def test_inventory_is_metadata_not_market_validation(self):
        self.fixture()
        code, data = self.run_check()
        self.assertEqual(code, 0)
        self.assertTrue(data['input_inventory']['input_files_present'])
        self.assertFalse(data['input_inventory']['official_market_data_validation'])
        self.assertEqual(data['input_inventory']['active_sources_checked'], 1)

    def test_changed_frozen_source_blocks(self):
        self.fixture()
        (self.root / 'strategy_lab/deploy_ready/candidate.py').write_text('# changed')
        code, data = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(data['input_inventory']['source_errors'][0]['reason'], 'SOURCE_SHA256_MISMATCH')

    def test_source_outside_root_blocks(self):
        entry = self.fixture()
        entry['canonical_source_file'] = '../outside.py'
        (self.root / 'strategy_lab/results/v3/historical_314_pipeline_stage2.json').write_text(json.dumps({'entries': [entry]}))
        code, data = self.run_check()
        self.assertEqual(code, 2)
        self.assertEqual(data['input_inventory']['source_errors'][0]['reason'], 'SOURCE_PATH_OUTSIDE_RESEARCH_ROOT')

class ExportInputTests(InputFixture, unittest.TestCase):
    def tearDown(self):
        Path(self.temp.name + '-parts.zip').unlink(missing_ok=True)
        shutil.rmtree(self.temp.name + '-parts', ignore_errors=True)
        super().tearDown()

    def export(self, split_bytes=None):
        destination = Path(self.temp.name + '-parts.zip')
        env = dict(os.environ, BBOT_LOCAL_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        if split_bytes is not None:
            env['BBOT_EXPORT_PART_BYTES'] = str(split_bytes)
        result = subprocess.run([sys.executable, '-B', str(REPO / 'strategy_lab/research_controller.py'), '--mode', 'managed-dry-run', '--export-inputs', str(destination)], env=env, capture_output=True, text=True, timeout=20)
        return result, destination

    def test_export_manifest_hashes_and_private_transport(self):
        import zipfile
        self.fixture()
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result, destination = self.export()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data['export']['created'])
        self.assertFalse(data['export']['public_upload_allowed'])
        with zipfile.ZipFile(destination) as archive:
            manifest = json.loads(archive.read('HANDOFF.json'))
            self.assertFalse(manifest['public_upload_allowed'])
            self.assertNotIn('strategy_config.json', archive.namelist())
            for name, row in manifest['files'].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), row['sha256'])
        for name, raw in before.items():
            self.assertEqual((self.root / name).read_bytes(), raw)

    def test_existing_archive_never_overwritten(self):
        self.fixture()
        destination = Path(self.temp.name + '-parts.zip')
        destination.write_bytes(b'existing archive')
        result, _ = self.export()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(destination.read_bytes(), b'existing archive')

    def test_credential_pattern_blocks_export(self):
        self.fixture()
        sensitive = self.root / 'strategy_lab/results/v3/example.json'
        sensitive.write_text(json.dumps({'credential': 'ghp_' + 'Z' * 36}))
        result, destination = self.export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EXPORT_CREDENTIAL_PATTERN_DETECTED', result.stderr)
        self.assertFalse(destination.exists())
        self.assertFalse(list(destination.parent.glob('.research-export-' + destination.stem + '-*.tmp')))

    def test_busy_worker_skips_export(self):
        import fcntl
        self.fixture()
        with (self.root / 'strategy_lab/results/v3/research_controller.lock').open('a+b') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, destination = self.export()
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(json.loads(result.stdout)['reason'], 'RESEARCH_ALREADY_RUNNING')
            self.assertFalse(destination.exists())

    def test_split_transfer_round_trip_and_hashes(self):
        import io
        import zipfile
        self.fixture()
        (self.root / 'data/candles/incompressible.parquet').write_bytes(os.urandom(2 * 1024**2 + 10000))
        result, destination = self.export(split_bytes=1024**2)
        self.assertEqual(result.returncode, 0, result.stderr)
        folder = destination.with_suffix('')
        try:
            self.assertFalse(destination.exists())
            transfer = json.loads((folder / 'TRANSFER.json').read_text())
            self.assertFalse(transfer['public_upload_allowed'])
            self.assertGreater(len(transfer['parts']), 1)
            combined = bytearray()
            for part in transfer['parts']:
                raw = (folder / part['name']).read_bytes()
                self.assertLessEqual(len(raw), 1024**2)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), part['sha256'])
                combined.extend(raw)
            self.assertEqual(hashlib.sha256(combined).hexdigest(), transfer['archive_sha256'])
            with zipfile.ZipFile(io.BytesIO(combined)) as archive:
                manifest = json.loads(archive.read('HANDOFF.json'))
                for name, row in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), row['sha256'])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_existing_split_directory_preserved(self):
        self.fixture()
        folder = Path(self.temp.name + '-parts')
        folder.mkdir()
        (folder / 'keep.txt').write_text('keep')
        result, _ = self.export()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(folder.is_dir())

    def test_runtime_external_fill_preserved_without_token_false_positive(self):
        import zipfile
        self.fixture()
        name = 'strategy_lab/results/v3/runtime_feedback.json'
        payload = {'schema': 'B_BOT_R4_RUNTIME_RESEARCH_FEEDBACK_V1', 'records': [{'symbol': 'ZKUSDT', 'leg_id': 'LEG_' + 'a' * 32, 'exit_fill_ids': ['EXTERNAL_TRADE:ZKUSDT:123456789:LEG_' + 'a' * 32]} ]}
        raw = json.dumps(payload).encode()
        (self.root / name).write_bytes(raw)
        result, destination = self.export()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(archive.read(name), raw)
        self.assertEqual((self.root / name).read_bytes(), raw)

    def test_token_in_fill_id_still_blocked(self):
        self.fixture()
        payload = {'schema': 'B_BOT_R4_RUNTIME_RESEARCH_FEEDBACK_V1', 'records': [{'exit_fill_ids': ['123456789:' + 'B' * 35]}]}
        (self.root / 'strategy_lab/results/v3/runtime_feedback.json').write_text(json.dumps(payload))
        result, destination = self.export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EXPORT_CREDENTIAL_PATTERN_DETECTED:strategy_lab/results/v3/runtime_feedback.json:RULE_4', result.stderr)
        self.assertFalse(destination.exists())

    def test_token_elsewhere_in_feedback_still_blocked(self):
        self.fixture()
        payload = {'schema': 'B_BOT_R4_RUNTIME_RESEARCH_FEEDBACK_V1', 'records': [{'symbol': 'ZKUSDT', 'leg_id': 'LEG_' + 'a' * 32, 'exit_fill_ids': ['EXTERNAL_TRADE:ZKUSDT:123456789:LEG_' + 'a' * 32], 'detail': '123456789:' + 'B' * 35}]}
        (self.root / 'strategy_lab/results/v3/runtime_feedback.json').write_text(json.dumps(payload))
        result, destination = self.export()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(destination.exists())

    def test_external_fill_with_wrong_record_binding_blocked(self):
        self.fixture()
        payload = {'schema': 'B_BOT_R4_RUNTIME_RESEARCH_FEEDBACK_V1', 'records': [{'symbol': 'BTCUSDT', 'leg_id': 'LEG_' + 'b' * 32, 'exit_fill_ids': ['EXTERNAL_TRADE:ZKUSDT:123456789:LEG_' + 'a' * 32]}]}
        (self.root / 'strategy_lab/results/v3/runtime_feedback.json').write_text(json.dumps(payload))
        result, destination = self.export()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(destination.exists())

    def test_external_fill_like_text_elsewhere_still_blocked(self):
        self.fixture()
        payload = {'schema': 'B_BOT_R4_RUNTIME_RESEARCH_FEEDBACK_V1', 'records': [{'symbol': 'ZKUSDT', 'leg_id': 'LEG_' + 'a' * 32, 'exit_fill_ids': [], 'detail': 'EXTERNAL_TRADE:ZKUSDT:123456789:LEG_' + 'a' * 32}]}
        (self.root / 'strategy_lab/results/v3/runtime_feedback.json').write_text(json.dumps(payload))
        result, destination = self.export()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(destination.exists())

    def instrumented_export(self, hook):
        destination = Path(self.temp.name + '-parts.zip')
        script = '''import os,runpy,sys,zipfile,shutil
from pathlib import Path
from unittest.mock import patch
root=Path(os.environ['BBOT_LOCAL_ROOT'])
controller=Path(sys.argv[1])
sys.path.insert(0,str(controller.parent))
sys.argv=[str(controller),'--mode','managed-dry-run','--export-inputs',sys.argv[2]]
''' + hook + "\nrunpy.run_path(str(controller),run_name='__main__')\n"
        env = dict(os.environ, BBOT_LOCAL_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', '-c', script, str(REPO/'strategy_lab/research_controller.py'), str(destination)], env=env, capture_output=True, text=True, timeout=20)
        return result, destination

    def test_original_update_during_compression_preserves_frozen_checkpoint(self):
        import zipfile
        self.fixture()
        ledger = self.root/'strategy_lab/results/v3/trial_ledger.json'
        original = ledger.read_bytes()
        hook = '''real_write=zipfile.ZipFile.writestr
changed=False
def write(archive,*args,**kwargs):
    global changed
    if not changed:
        changed=True
        target=root/'strategy_lab/results/v3/trial_ledger.json'
        target.write_text('{"schema":"B_BOT_V3_TRIAL_LEDGER_V1","entries":[{"new_cycle":true}]}')
    return real_write(archive,*args,**kwargs)
zipfile.ZipFile.writestr=write
'''
        result, destination = self.instrumented_export(hook)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(ledger.read_bytes(), original)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(archive.read('strategy_lab/results/v3/trial_ledger.json'), original)
            manifest = json.loads(archive.read('HANDOFF.json'))
            self.assertIn('snapshot_completed_at', manifest)
            for name, row in manifest['files'].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), row['sha256'])

    def test_original_update_during_snapshot_fails_with_path(self):
        self.fixture()
        hook = '''real_write=Path.write_bytes
changed=False
def write(path,data):
    global changed
    result=real_write(path,data)
    if not changed and path.parent.name.startswith('.research-snapshot-'):
        changed=True
        target=root/'strategy_lab/results/v3/trial_ledger.json'
        target.write_text('{"schema":"B_BOT_V3_TRIAL_LEDGER_V1","entries":[{"concurrent":true}]}')
    return result
Path.write_bytes=write
'''
        result, destination = self.instrumented_export(hook)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EXPORT_INPUT_CHANGED_RETRY:strategy_lab/results/v3/trial_ledger.json', result.stderr)
        self.assertFalse(destination.exists())

    def test_snapshot_canonical_source_revalidated(self):
        self.fixture()
        hook = '''real_usage=shutil.disk_usage
def usage(path):
    target=root/'strategy_lab/deploy_ready/candidate.py'
    target.write_text('# concurrent new version\\n')
    return real_usage(path)
shutil.disk_usage=usage
'''
        result, destination = self.instrumented_export(hook)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EXPORT_SNAPSHOT_CANONICAL_SOURCE_MISMATCH', result.stderr)
        self.assertFalse(destination.exists())

    def test_snapshot_low_disk_fails_before_copy(self):
        self.fixture()
        hook = "shutil.disk_usage=lambda path: __import__('types').SimpleNamespace(free=0)\n"
        result, destination = self.instrumented_export(hook)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EXPORT_SNAPSHOT_DISK_SPACE_REQUIRED', result.stderr)
        self.assertFalse(destination.exists())

    def test_observation_updates_do_not_invalidate_checkpoint_capture(self):
        import zipfile
        self.fixture()
        names = ['strategy_lab/results/master_handoff_system_latest.json', 'strategy_lab/results/v3/collector_heartbeat.json']
        for name in names:
            (self.root/name).write_text('{"updated_at_epoch":1}')
        hook = '''real_write=Path.write_bytes
changed=False
def write(path,data):
    global changed
    result=real_write(path,data)
    if not changed and path.parent.name.startswith('.research-snapshot-'):
        changed=True
        for name in ('strategy_lab/results/master_handoff_system_latest.json','strategy_lab/results/v3/collector_heartbeat.json'):
            (root/name).write_text('{"updated_at_epoch":2}')
    return result
Path.write_bytes=write
'''
        result, destination = self.instrumented_export(hook)
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(destination) as archive:
            manifest = json.loads(archive.read('HANDOFF.json'))
            for name in names:
                raw = archive.read(name)
                receipt = manifest['observation_receipts'][name]
                self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
                self.assertEqual(receipt['consistency'], 'PER_FILE_OBSERVATION_ONLY')
                self.assertFalse(receipt['freshness_claimed'])
                self.assertEqual(json.loads(raw)['updated_at_epoch'], 2)

    def test_observation_still_scanned_for_credentials(self):
        self.fixture()
        (self.root/'strategy_lab/results/v3/collector_heartbeat.json').write_text(json.dumps({'secret':'ghp_'+'Z'*36}))
        result, destination = self.export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EXPORT_CREDENTIAL_PATTERN_DETECTED:strategy_lab/results/v3/collector_heartbeat.json', result.stderr)
        self.assertFalse(destination.exists())

    def test_malformed_observation_not_exported(self):
        self.fixture()
        (self.root/'strategy_lab/results/v3/collector_heartbeat.json').write_text('{incomplete')
        result, destination = self.export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EXPORT_OBSERVATION_UNSTABLE_RETRY:', result.stderr)
        self.assertFalse(destination.exists())

    @unittest.skipIf(os.name == 'nt', 'Linux flock contention test')
    def test_export_waits_for_existing_research_lock(self):
        self.fixture()
        hook = '''import subprocess,time
lock=root/'strategy_lab/results/v3/research_controller.lock'
ready=root/'lock_ready'
child="import fcntl,time;from pathlib import Path;f=open("+repr(str(lock))+",'a+b');fcntl.flock(f,fcntl.LOCK_EX);Path("+repr(str(ready))+").write_text('ready');time.sleep(0.5)"
process=subprocess.Popen([sys.executable,'-c',child])
deadline=time.monotonic()+3
while not ready.exists():
    if time.monotonic()>deadline:raise RuntimeError('fixture lock not ready')
    time.sleep(0.01)
sys.argv+=['--export-lock-wait-seconds','3']
'''
        result, destination = self.instrumented_export(hook)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(destination.exists())
