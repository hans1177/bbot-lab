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
        Path(self.temp.name + '.zip').unlink(missing_ok=True)
        super().tearDown()

    def export(self):
        destination = Path(self.temp.name + '.zip')
        env = dict(os.environ, BBOT_LOCAL_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE='1')
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
        destination = Path(self.temp.name + '.zip')
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
