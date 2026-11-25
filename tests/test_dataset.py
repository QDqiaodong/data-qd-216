import tempfile
import unittest
from pathlib import Path

from load_balance import TrajectoryDataset


def growth_scenario(aft1="2.08", aft2="3.36"):
    """The issuable trajectory: 100 kg at arm 2 m, then 100 kg more at arm 4 m.

    With the default aft limits 2.08/3.36 the segment is tangent to the aft
    limit at 125 kg and stays eligible; with 2/3 it violates mid-segment.
    """
    return {
        "stations": [
            {"id": "s1", "arm_m": "2", "max_weight_kg": "500"},
            {"id": "s2", "arm_m": "4", "max_weight_kg": "500"},
        ],
        "envelope": [
            {"weight_kg": "100", "forward_cg_m": "1", "aft_cg_m": aft1},
            {"weight_kg": "200", "forward_cg_m": "1", "aft_cg_m": aft2},
        ],
        "frames": [
            {"loads": [{"id": "a", "station_id": "s1", "weight_kg": "100"}]},
            {"loads": [
                {"id": "a", "station_id": "s1", "weight_kg": "100"},
                {"id": "b", "station_id": "s2", "weight_kg": "100"},
            ]},
        ],
    }


def renamed_ids(data, frame_index, mapping):
    for row in data["frames"][frame_index]["loads"]:
        if row["id"] in mapping:
            row["id"] = mapping[row["id"]]
    return data


class DatasetTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "dataset.json"

    def open(self):
        return TrajectoryDataset(self.path)


class LifecycleTests(DatasetTestCase):
    def test_correction_revokes_receipts_and_audit_survives_reopen(self):
        ds = self.open()
        analysis = ds.put_scenario("S", 1, growth_scenario())
        self.assertTrue(analysis["eligible"])
        # a second scenario whose receipt stays usable through every S correction
        ds.put_scenario("T", 1, growth_scenario())
        ds.issue("RT", "T", 1)

        receipt_r = ds.issue("R", "S", 1)
        self.assertEqual(set(receipt_r), {"request_id", "scenario_id", "revision", "analysis"})
        self.assertEqual((receipt_r["scenario_id"], receipt_r["revision"]), ("S", 1))
        self.assertTrue(receipt_r["analysis"]["eligible"])
        sample = ds.consume("R")
        self.assertEqual((sample["scenario_id"], sample["revision"]), ("S", 1))
        self.assertEqual(sample["data"]["frames"][1]["loads"][1]["id"], "b")
        self.assertEqual(sample["analysis"], receipt_r["analysis"])
        self.assertEqual(ds.consume("R"), sample)  # a live receipt is repeatable

        # correction: only load ids of one frame change; numbers and eligibility identical
        revised = renamed_ids(growth_scenario(), 1, {"a": "a2", "b": "b2"})
        analysis2 = ds.put_scenario("S", 2, revised)
        self.assertEqual(analysis2, analysis)

        with self.assertRaises(ValueError):
            ds.consume("R")  # the old receipt is revoked immediately
        self.assertEqual(ds.get_receipt("R"), receipt_r)  # audit still returns it unchanged
        self.assertEqual(ds.issue("R", "S", 1), receipt_r)  # same-binding retry, not re-issued
        with self.assertRaises(ValueError):
            ds.issue("R", "S", 2)  # rebinding the same id is rejected
        with self.assertRaises(ValueError):
            ds.issue("R", "T", 1)

        receipt_r2 = ds.issue("R2", "S", 2)
        sample2 = ds.consume("R2")
        self.assertEqual([row["id"] for row in sample2["data"]["frames"][1]["loads"]], ["a2", "b2"])

        # a structurally broken correction is rejected and withdraws nothing
        broken = growth_scenario()
        broken["envelope"][1]["weight_kg"] = "100"  # envelope weights must increase
        with self.assertRaises(ValueError):
            ds.put_scenario("S", 3, broken)
        self.assertEqual(ds.consume("R2"), sample2)

        # an ineligible but structurally valid correction still becomes current
        analysis3 = ds.put_scenario("S", 3, growth_scenario(aft1="2", aft2="3"))
        self.assertFalse(analysis3["eligible"])
        with self.assertRaises(ValueError):
            ds.consume("R2")  # old receipt stays revoked
        with self.assertRaises(ValueError):
            ds.issue("R3", "S", 3)  # nothing can be issued against an ineligible revision
        self.assertEqual(ds.get_receipt("R2"), receipt_r2)  # audit intact
        self.assertEqual(ds.consume("RT")["scenario_id"], "T")  # other scenarios unaffected

        ds.close()
        reopened = self.open()
        self.assertEqual(reopened.get_receipt("R"), receipt_r)
        self.assertEqual(reopened.issue("R", "S", 1), receipt_r)  # retry rule survives reopen
        with self.assertRaises(ValueError):
            reopened.consume("R")
        with self.assertRaises(ValueError):
            reopened.consume("R2")
        with self.assertRaises(ValueError):
            reopened.issue("R3b", "S", 3)  # current revision is ineligible
        self.assertEqual(reopened.consume("RT")["scenario_id"], "T")

        # a new eligible revision restores issuability for new requests only
        reopened.put_scenario("S", 4, growth_scenario())
        with self.assertRaises(ValueError):
            reopened.consume("R2")  # old receipts are never migrated
        receipt_r4 = reopened.issue("R4", "S", 4)
        self.assertEqual(reopened.consume("R4")["analysis"], receipt_r4["analysis"])
        reopened.close()

    def test_corrections_are_scoped_per_scenario(self):
        ds = self.open()
        ds.put_scenario("A", 1, growth_scenario())
        ds.put_scenario("B", 1, growth_scenario())
        ds.issue("RA", "A", 1)
        ds.issue("RB", "B", 1)
        ds.put_scenario("A", 2, growth_scenario())
        with self.assertRaises(ValueError):
            ds.consume("RA")
        self.assertEqual(ds.consume("RB")["scenario_id"], "B")
        ds.close()


class RevisionRuleTests(DatasetTestCase):
    def test_revision_sequence_is_enforced(self):
        ds = self.open()
        with self.assertRaises(ValueError):
            ds.put_scenario("S", 2, growth_scenario())  # first revision must be 1
        with self.assertRaises(ValueError):
            ds.put_scenario("S", 0, growth_scenario())
        with self.assertRaises(ValueError):
            ds.put_scenario("S", "1", growth_scenario())
        with self.assertRaises(ValueError):
            ds.put_scenario("S", True, growth_scenario())
        ds.put_scenario("S", 1, growth_scenario())
        with self.assertRaises(ValueError):
            ds.put_scenario("S", 1, growth_scenario())  # already committed
        with self.assertRaises(ValueError):
            ds.put_scenario("S", 3, growth_scenario())  # skips a revision
        ds.put_scenario("S", 2, growth_scenario())
        ds.close()
        with self.assertRaises(ValueError):
            self.open().put_scenario("S", 4, growth_scenario())  # sequence survives reopen

    def test_invalid_input_is_rejected_without_side_effects(self):
        ds = self.open()
        with self.assertRaises(ValueError):
            ds.put_scenario("", 1, growth_scenario())
        with self.assertRaises(ValueError):
            ds.put_scenario("S", 1, {"stations": [], "loads": []})  # not a trajectory
        with self.assertRaises(ValueError):
            ds.put_scenario("S", 1, growth_scenario() | {"frames": []})  # too few frames
        non_serializable = growth_scenario()
        non_serializable["note"] = {"blob": object()}
        with self.assertRaises(ValueError):
            ds.put_scenario("S", 1, non_serializable)
        ds.put_scenario("S", 1, growth_scenario())  # nothing above created the scenario
        ds.close()


class IssueRuleTests(DatasetTestCase):
    def test_rejected_requests_do_not_reserve_the_id(self):
        ds = self.open()
        ds.put_scenario("S", 1, growth_scenario())
        with self.assertRaises(ValueError):
            ds.issue("", "S", 1)
        with self.assertRaises(ValueError):
            ds.issue("X", "", 1)
        with self.assertRaises(ValueError):
            ds.issue("X", "S", "1")
        with self.assertRaises(ValueError):
            ds.issue("X", "missing", 1)  # unknown scenario
        with self.assertRaises(ValueError):
            ds.issue("X", "S", 2)  # not the current revision
        receipt = ds.issue("X", "S", 1)  # the id is still free after the rejections
        self.assertEqual(receipt["request_id"], "X")
        self.assertEqual(ds.issue("X", "S", 1), receipt)  # same-binding retry
        ds.close()

    def test_issue_requires_eligible_current_revision(self):
        ds = self.open()
        ds.put_scenario("S", 1, growth_scenario(aft1="2", aft2="3"))  # ineligible from the start
        with self.assertRaises(ValueError):
            ds.issue("R", "S", 1)
        ds.put_scenario("S", 2, growth_scenario())  # corrected to the tangent-eligible version
        receipt = ds.issue("R", "S", 2)  # the rejected id was never reserved
        self.assertEqual(receipt["revision"], 2)
        ds.close()

    def test_receipt_never_carries_raw_loads(self):
        ds = self.open()
        ds.put_scenario("S", 1, growth_scenario())
        receipt = ds.issue("R", "S", 1)
        self.assertEqual(set(receipt), {"request_id", "scenario_id", "revision", "analysis"})
        for frame in receipt["analysis"]["frames"]:
            self.assertNotIn("loads", frame)
        ds.close()

    def test_unknown_requests_raise(self):
        ds = self.open()
        with self.assertRaises(ValueError):
            ds.get_receipt("nope")
        with self.assertRaises(ValueError):
            ds.consume("nope")
        with self.assertRaises(ValueError):
            ds.consume("")
        ds.close()

    def test_returned_objects_are_detached_copies(self):
        ds = self.open()
        analysis = ds.put_scenario("S", 1, growth_scenario())
        analysis["eligible"] = "corrupted"
        receipt = ds.issue("R", "S", 1)
        self.assertTrue(receipt["analysis"]["eligible"])
        receipt["analysis"]["eligible"] = "corrupted"
        self.assertTrue(ds.get_receipt("R")["analysis"]["eligible"])
        sample = ds.consume("R")
        sample["data"]["frames"][0]["loads"][0]["weight_kg"] = "999"
        self.assertEqual(ds.consume("R")["data"]["frames"][0]["loads"][0]["weight_kg"], "100")
        ds.close()

    def test_close_and_context_manager(self):
        with self.open() as ds:
            ds.put_scenario("S", 1, growth_scenario())
        with self.assertRaises(ValueError):
            ds.consume("R")
        self.assertTrue(self.open().put_scenario("S", 2, growth_scenario())["eligible"])


if __name__ == "__main__":
    unittest.main()
