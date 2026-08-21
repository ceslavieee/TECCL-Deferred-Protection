import unittest

from teccl.input_data import GurobiParams, InstanceParams
from teccl.examples.dynamic_admission_pilot import (
    TOPOLOGY_CONFIGS,
    certified_template_provenance,
    generate_trace,
)


class DynamicTemplateProvenanceTest(unittest.TestCase):
    def test_no_rel_feasibility_search_is_opt_in(self):
        self.assertEqual(0.0, GurobiParams().no_rel_heur_work)

    def test_fixed_working_tree_candidate_is_opt_in(self):
        self.assertEqual("", InstanceParams().fixed_working_tree_schedule)

    def test_main_topology_templates_are_current_physical_certificates(self):
        for topology in ("DCN4WAN", "InterDC8"):
            config = TOPOLOGY_CONFIGS[topology]
            self.assertEqual(25.0, config["chunk_size_gb"])
            self.assertEqual(2.0, config["epoch_duration_seconds"])
            self.assertEqual("EXACT", config["failure_model"])
            self.assertEqual(
                "theorem_backed_construction",
                config["residual_failure_time_validation"],
            )
            for policy, path in config["cached_templates"].items():
                self.assertIn("multicast_tree_pair_physical_25gb", str(path))
                provenance = certified_template_provenance(
                    topology,
                    policy,
                    path,
                    config["detection_certificate"],
                    config["detection_delay_epochs"],
                    config["expected_failure_scenarios"],
                )
                self.assertTrue(provenance["fixed_plan_boundary_certified"])
                self.assertEqual(64, len(provenance["template_sha256"]))
                self.assertEqual(
                    config["expected_failure_scenarios"],
                    provenance["failure_scenarios"],
                )

    def test_rejects_detection_delay_beyond_fixed_plan_certificate(self):
        config = TOPOLOGY_CONFIGS["InterDC8"]
        with self.assertRaisesRegex(AssertionError, "exceeds"):
            certified_template_provenance(
                "InterDC8",
                "early_dpp",
                config["cached_templates"]["early_dpp"],
                config["detection_certificate"],
                3,
                config["expected_failure_scenarios"],
            )

    def test_trace_hash_covers_physical_workload_and_detection_delay(self):
        trace = generate_trace(
            7,
            2,
            0.1,
            "InterDC8",
            40,
            25.0,
            2.0,
            1,
        )
        self.assertEqual(25.0, trace["chunk_size_gb"])
        self.assertEqual(2.0, trace["epoch_duration_seconds"])
        self.assertEqual(1, trace["detection_delay_epochs"])
        self.assertEqual(64, len(trace["sha256"]))


if __name__ == "__main__":
    unittest.main()
