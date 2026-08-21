import json
from pathlib import Path
import tempfile
import unittest

from teccl.dynamic_admission import CapacityTimeLedger, ScheduleTemplate


def schedule_payload():
    return {
        "8-Final_Deadline_Epoch": 8,
        "10-Working_Flows": [
            "Chunk 0 from 0 traveled over 0->1 in epoch 0",
        ],
        "11b-Reserved_Backup_Slots": [
            "Chunk 0 from 0 reserves 1->2 in epoch 4",
        ],
        "14h-Chunk_Working_Completion_Profile": [
            {"source": 0, "chunk": 0, "working_completion_epoch": 2},
        ],
        "14i-Flow_Occupancy_Epochs_By_Link": [
            {"link": "0->1", "occupied_epochs_per_flow": 2},
            {"link": "1->2", "occupied_epochs_per_flow": 2},
        ],
    }


class DynamicAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "schedule.json"
        self.path.write_text(json.dumps(schedule_payload()))
        self.template = ScheduleTemplate.from_path(self.path, "test")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_expands_full_occupancy_and_blocks_atomically(self):
        ledger = CapacityTimeLedger()
        first = ledger.try_admit("first", 0, self.template)
        before = ledger.occupied_cell_count
        second = ledger.try_admit("second", 1, self.template)
        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(before, ledger.occupied_cell_count)
        self.assertEqual(("first",), ledger.accepted_request_ids())
        ledger.assert_safe()

    def test_releases_future_protection_after_working_completion(self):
        ledger = CapacityTimeLedger()
        ledger.try_admit("first", 0, self.template)
        self.assertIn((1, 2, 4), ledger.occupied_cells())
        released = ledger.release_completed_protection(2)
        self.assertEqual(1, released)
        self.assertNotIn((1, 2, 4), ledger.occupied_cells())
        self.assertIn((0, 1, 0), ledger.occupied_cells())

    def test_rejects_internal_template_capacity_conflict(self):
        payload = schedule_payload()
        payload["11b-Reserved_Backup_Slots"] = [
            "Chunk 0 from 0 reserves 0->1 in epoch 1",
        ]
        self.path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "exceeds unit cell capacity"):
            ScheduleTemplate.from_path(self.path, "bad")

    def test_exports_request_local_residual_occupancy(self):
        ledger = CapacityTimeLedger()
        ledger.try_admit("first", 3, self.template)
        payload = ledger.residual_occupancy_payload(4, 5)
        self.assertEqual(4, payload["arrival_epoch"])
        self.assertEqual(5, payload["horizon_epochs"])
        self.assertIn(
            {"src": 0, "dst": 1, "epoch": 0, "occupancy": 1},
            payload["epochs"],
        )
        self.assertEqual(64, len(payload["sha256"]))


if __name__ == "__main__":
    unittest.main()
