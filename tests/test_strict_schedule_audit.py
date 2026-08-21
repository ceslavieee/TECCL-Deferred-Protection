import unittest

from teccl.examples.audit_strict_dedicated_schedule import (
    missing_tree_terminals,
    robust_reservation_timing_violations,
    tree_structure_violations,
)


class StrictScheduleTreeAuditTest(unittest.TestCase):
    def test_accepts_rooted_tree_with_repeated_time_slots(self):
        flows = {
            (0, 0, 1, 0, 1),
            (0, 0, 1, 0, 2),
            (0, 1, 2, 0, 3),
            (0, 1, 3, 0, 3),
        }
        self.assertEqual([], tree_structure_violations(flows))

    def test_rejects_multiple_parents(self):
        flows = {
            (0, 0, 1, 0, 1),
            (0, 0, 2, 0, 1),
            (0, 1, 3, 0, 2),
            (0, 2, 3, 0, 2),
        }
        violations = tree_structure_violations(flows)
        self.assertTrue(
            any(row[2] == "multiple_parents" for row in violations),
            violations,
        )

    def test_rejects_disconnected_cycle(self):
        flows = {
            (0, 0, 1, 0, 1),
            (0, 2, 3, 0, 2),
            (0, 3, 2, 0, 3),
        }
        violations = tree_structure_violations(flows)
        self.assertTrue(
            any(row[2] == "not_rooted_at_source" for row in violations),
            violations,
        )

    def test_rejects_tree_missing_allgather_terminal(self):
        flows = {
            (0, 0, 1, 0, 1),
            (0, 1, 2, 0, 2),
        }
        violations = missing_tree_terminals(
            flows,
            commodities={(0, 0)},
            terminals={0, 1, 2, 3},
        )
        self.assertEqual(
            [(0, 0, "missing_terminals", [3])],
            violations,
        )

    def test_rejects_empty_backup_tree(self):
        violations = missing_tree_terminals(
            set(),
            commodities={(0, 0), (1, 0)},
            terminals={0, 1},
        )
        self.assertEqual(
            {
                (0, 0, "missing_terminals", (1,)),
                (1, 0, "missing_terminals", (0,)),
            },
            {
                (source, chunk, kind, tuple(missing))
                for source, chunk, kind, missing in violations
            },
        )

    def test_robust_reservation_accounts_for_detection_delay(self):
        reservations = {
            (0, 0, 1, 0, 5),
            (1, 1, 0, 0, 6),
        }
        violations = robust_reservation_timing_violations(
            reservations,
            {(0, 0): 5, (1, 0): 5},
            detection_delay_epochs=2,
        )
        self.assertEqual([(0, 0, 1, 0, 5)], violations)


if __name__ == "__main__":
    unittest.main()
