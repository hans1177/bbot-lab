"""Fail-closed regression for historical CANARY policy parity; no live writes."""
import ast
import hashlib
import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def function_from_source(path, names, namespace):
    source = ast.parse(path.read_text(encoding="utf-8-sig"))
    nodes = [
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


class ShadowLegacyPolicyParityTests(unittest.TestCase):
    def setUp(self):
        self.version = "SV_STRATEGY_HISTORICAL"
        self.instance_id = "SI_HISTORICAL"
        self.strategy = "V3_HISTORICAL"
        self.behavior = {
            "trailing": {
                "enabled": True, "mode": "FIXED_BASELINE",
                "dynamic_adjustment": False, "start_pct": 3,
                "callback_pct": 1.5,
            },
            "state_reentry": {
                "enabled": True, "dynamic_policy": False,
                "mode": "CURRENT_STATE", "next_check_tf": "1H",
            },
            "opposite_exit": {},
            "opposite_reopen": {},
        }
        self.config = {
            "trailing": dict(self.behavior["trailing"]),
            "state_reentry": dict(self.behavior["state_reentry"]),
        }
        self.snapshot = {
            "policy_versions": {"EXIT": "PV_EXIT_BASELINE", "REENTRY": "PV_REENTRY_BASELINE"},
            "behavior_policy": self.behavior,
        }
        self.frame = {"strategy_instance_id": self.instance_id}
        self.request = {
            "strategy_version": self.version,
            "strategy_policy_snapshot": self.snapshot,
            "strategy_policy_bundle_version": self.bundle(),
            "research_evidence": {},
        }
        self.instance = {
            "strategy_instance_id": self.instance_id,
            "strategy_id": self.strategy,
            "timeframe": "1H",
            "exact_strategy_version": self.version,
            "strategy_policy_bundle_version": self.bundle(),
            "policy_snapshot": self.snapshot,
            "status": "CANARY",
            "approved_for_server_release": True,
            "canary_authorized": True,
            "evidence": {"source": "R4_EXISTING_DEPLOYMENT_MIGRATION"},
            "career": [
                {"event": "CANARY", "evidence": {
                    "reason": "R4_EXISTING_CANARY_EXACT_INSTANCE_SHADOW_PASS"
                }},
            ],
        }
        self.registry = {
            "strategy_instances": {self.instance_id: self.instance},
            "strategy_versions": {self.version: {
                "status": "CANARY",
                "payload": {"strategy": self.strategy, "timeframe": "1H"},
            }},
        }
        self.ns = {
            "json": json, "hashlib": hashlib, "math": math, "ROOT": ROOT,
            "VERSION_REGISTRY": Path("dummy_registry.json"),
            "load_json": self.read_fixture,
        }
        function_from_source(
            ROOT / "strategy_lab/v3_shadow_engine.py",
            {
                "_bundle_version_from_snapshot",
                "_legacy_frozen_baseline_policy_pass",
                "_strategy_instance_policy_snapshot_check",
                "shadow_pass_from_gate",
            },
            self.ns,
        )
        self.check = self.ns["_strategy_instance_policy_snapshot_check"]
        self.pass_gate = self.ns["shadow_pass_from_gate"]

    def read_fixture(self, path, default=None):
        if str(path).endswith("dummy_registry.json"):
            return self.registry
        if str(path).endswith("strategy_config.json"):
            return self.config
        return default if default is not None else {}

    def bundle(self):
        raw = json.dumps(
            self.snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "PB_" + hashlib.sha256(raw).hexdigest()[:32]

    def evidence(self):
        return self.check(self.frame, self.request)

    def gate(self, detail):
        return {
            "runtime_errors": 0,
            "closed_bar_timing_pass": True,
            "signal_logic_parity_pass": True,
            "state_transition_pass": True,
            "order_simulation_cost_sanity_pass": True,
            "strategy_instance_policy_snapshot_pass": detail["pass"],
            "individual_trailing_snapshot_binding_pass": detail["trailing_binding_pass"],
            "individual_reentry_snapshot_binding_pass": detail["reentry_binding_pass"],
            "policy_contract_mode": detail["policy_contract_mode"],
            "legacy_frozen_baseline_policy_pass": detail["legacy_frozen_baseline_policy_pass"],
            "order_quantity_calculation_pass": True,
            "duplicate_or_missing_action_integrity_pass": True,
            "portfolio_allocation_gate_integration_pass": True,
            "complete_lifecycles": 0,
            "replay_dryrun_state_transition_pass": True,
            "live_closed_bar_confirmation_pass": True,
        }

    def test_historical_migration_checks_baseline_without_individual_credit(self):
        passed, detail = self.evidence()
        self.assertTrue(passed, detail)
        self.assertEqual(detail["policy_contract_mode"], "LEGACY_FROZEN_BASELINE_V1")
        self.assertTrue(detail["legacy_frozen_baseline_policy_pass"])
        self.assertFalse(detail["trailing_binding_pass"])
        self.assertFalse(detail["reentry_binding_pass"])
        self.assertTrue(self.pass_gate(self.gate(detail)))

    def test_historical_provisional_requires_recorded_full_and_blind(self):
        self.instance["evidence"] = {
            "source": "V3_DEPLOYMENT_CREATE_PROVISIONAL",
            "full_validation_pass": True, "sealed_blind_pass": True,
        }
        self.instance["career"][0]["evidence"]["reason"] = "MINIMAL_SHADOW_RUNTIME_GATE_PASS"
        self.assertTrue(self.evidence()[0])
        self.instance["evidence"]["sealed_blind_pass"] = False
        self.assertFalse(self.evidence()[0])

    def test_new_main_cannot_enter_legacy_path(self):
        self.request["pipeline_contract"] = "NEW_MAIN_LOCAL_SHADOW_V1"
        self.assertFalse(self.evidence()[0])

    def test_missing_canary_provenance_denied(self):
        self.instance["career"] = []
        self.assertFalse(self.evidence()[0])
        self.instance["career"] = [{"event": "CANARY", "evidence": {"reason": "FAKE"}}]
        self.assertFalse(self.evidence()[0])

    def test_unapproved_instance_denied(self):
        self.instance["approved_for_server_release"] = False
        self.assertFalse(self.evidence()[0])

    def test_wrong_tier_denied(self):
        self.instance["status"] = "LOCAL_SHADOW"
        self.assertFalse(self.evidence()[0])

    def test_wrong_version_payload_denied(self):
        self.registry["strategy_versions"][self.version]["payload"]["strategy"] = "OTHER"
        self.assertFalse(self.evidence()[0])

    def test_active_baseline_change_denied(self):
        self.config["trailing"]["start_pct"] = 4
        self.assertFalse(self.evidence()[0])

    def test_unsafe_baseline_denied_even_when_config_matches(self):
        self.behavior["trailing"]["callback_pct"] = 4
        self.config["trailing"]["callback_pct"] = 4
        self.request["strategy_policy_bundle_version"] = self.bundle()
        self.instance["strategy_policy_bundle_version"] = self.bundle()
        self.assertFalse(self.evidence()[0])

    def test_partial_individual_binding_cannot_fallback(self):
        self.behavior["trailing"]["individual_strategy_binding"] = False
        self.config["trailing"]["individual_strategy_binding"] = False
        self.request["strategy_policy_bundle_version"] = self.bundle()
        self.instance["strategy_policy_bundle_version"] = self.bundle()
        self.assertFalse(self.evidence()[0])

    def test_selected_optimization_cannot_fallback(self):
        self.request["research_evidence"]["individual_trailing"] = {
            "start_pct": 3, "callback_pct": 1.5,
        }
        self.assertFalse(self.evidence()[0])

    def test_wrong_bundle_hash_denied(self):
        self.request["strategy_policy_bundle_version"] = "PB_FAKE"
        self.assertFalse(self.evidence()[0])

    def test_legacy_does_not_bypass_runtime_failures(self):
        _, detail = self.evidence()
        gate = self.gate(detail)
        for key in (
            "runtime_errors", "closed_bar_timing_pass",
            "portfolio_allocation_gate_integration_pass",
            "live_closed_bar_confirmation_pass",
        ):
            bad = dict(gate)
            bad[key] = 1 if key == "runtime_errors" else False
            self.assertFalse(self.pass_gate(bad), key)

    def test_individual_policy_gate_still_strict(self):
        gate = self.gate(self.evidence()[1])
        gate["policy_contract_mode"] = "INDIVIDUAL_STRATEGY_V1"
        gate["legacy_frozen_baseline_policy_pass"] = False
        self.assertFalse(self.pass_gate(gate))
        gate["individual_trailing_snapshot_binding_pass"] = True
        gate["individual_reentry_snapshot_binding_pass"] = True
        self.assertTrue(self.pass_gate(gate))

    def test_release_approval_also_requires_explicit_contract(self):
        data = {
            "pass": True, "version_id": self.version,
            "strategy_instance_id": self.instance_id,
            "real_order": False, "server_mutation": False,
            "role": "LOCAL_RESEARCH",
            "minimal_runtime_gate": self.gate(self.evidence()[1]),
        }
        ns = {
            "research_architecture_v3": lambda: {"shadow": {
                "mandatory_minimal_runtime_gate": True,
                "profitability_gate": False,
                "strategy_instance_scoped": True,
            }},
            "evidence_pass": lambda *args: True,
            "load": lambda *args: data,
            "evidence_file": lambda *args: Path("shadow.json"),
        }
        function_from_source(
            ROOT / "strategy_lab/local_release_approval.py",
            {"minimal_shadow_pass"},
            ns,
        )
        valid = ns["minimal_shadow_pass"]
        self.assertTrue(valid(self.version, self.instance_id))
        data["minimal_runtime_gate"]["legacy_frozen_baseline_policy_pass"] = False
        self.assertFalse(valid(self.version, self.instance_id))


if __name__ == "__main__":
    unittest.main()
