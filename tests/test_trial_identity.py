import importlib.util
import tempfile
import unittest
from pathlib import Path


class TrialIdentityTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            'isolated_knowledge', Path(__file__).resolve().parents[1]
            / 'strategy_lab/v3_research_knowledge.py')
        self.k = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.k)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.k.KNOWLEDGE = root / 'knowledge.json'
        self.k.TRIALS = root / 'trials.json'

    def reserve(self, version='EV_A', digest='a' * 64):
        return self.k.reserve_trial(
            'historical', 'strategy', '1H', 'HISTORICAL_HARD_PASS_RECHECK',
            exact_strategy_version=version,
            source_file='strategy_lab/results/v3/source.py',
            source_sha256=digest)

    def completed(self):
        trial = self.reserve()
        self.k.complete_trial(trial, 'result.json', 'PASS')
        return trial

    def test_identity_survives_completion_and_reload(self):
        self.completed()
        _, ledger = self.k.ensure()
        row = ledger['entries'][0]
        self.assertEqual(row['event'], 'COMPLETED')
        self.assertEqual(row['exact_strategy_version'], 'EV_A')
        self.assertEqual(row['source_sha256'], 'a' * 64)
        self.assertEqual(row['source_file'], 'strategy_lab/results/v3/source.py')

    def test_exact_completed_result_cannot_run_again(self):
        self.completed()
        with self.assertRaisesRegex(RuntimeError, 'DUPLICATE_COMPLETED'):
            self.reserve()

    def test_renaming_or_mutating_same_version_does_not_bypass_guard(self):
        self.completed()
        for version, digest in [('EV_B', 'a' * 64), ('EV_A', 'b' * 64)]:
            with self.subTest(version=version):
                with self.assertRaisesRegex(RuntimeError, 'DUPLICATE_COMPLETED'):
                    self.reserve(version, digest)

    def test_distinct_child_can_be_researched(self):
        self.completed()
        self.assertEqual(self.reserve('EV_B', 'b' * 64), 2)

    def test_legacy_completed_identity_is_fail_closed(self):
        trial = self.k.reserve_trial('old', 'strategy', '1H', 'HISTORICAL_HARD_PASS_RECHECK')
        self.k.complete_trial(trial, 'old.json', 'PASS')
        with self.assertRaisesRegex(RuntimeError, 'DUPLICATE_COMPLETED'):
            self.reserve()

    def test_incomplete_trial_does_not_claim_completion(self):
        self.reserve()
        self.assertEqual(self.reserve(), 2)


if __name__ == '__main__':
    unittest.main()
