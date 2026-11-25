import tempfile
import unittest
from pathlib import Path
from unittest import mock

import load_balance.dataset as dataset_module
from load_balance import TrajectoryDataset
from tests.test_dataset import growth_scenario


def crash_before_commit():
    """The process dies before the atomic rename: nothing was committed."""
    return mock.patch.object(dataset_module, "_durably_replace",
                             side_effect=OSError("simulated crash before commit"))


def crash_after_commit():
    """The rename happened but the caller never saw the response."""
    real = dataset_module._durably_replace

    def replace_then_crash(src, dst):
        real(src, dst)
        raise OSError("simulated crash after commit")

    return mock.patch.object(dataset_module, "_durably_replace", side_effect=replace_then_crash)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "dataset.json"

    def open(self):
        return TrajectoryDataset(self.path)

    def test_crash_before_issue_commit_reserves_nothing(self):
        ds = self.open()
        ds.put_scenario("S", 1, growth_scenario())
        with crash_before_commit():
            with self.assertRaises(OSError):
                ds.issue("R", "S", 1)
        # the live object never adopted the failed state: the id is still free
        receipt = ds.issue("R", "S", 1)
        ds.close()
        reopened = self.open()
        self.assertEqual(reopened.get_receipt("R"), receipt)  # exactly one receipt exists
        self.assertEqual(reopened.consume("R")["revision"], 1)
        reopened.close()

    def test_committed_issue_survives_a_lost_response(self):
        ds = self.open()
        ds.put_scenario("S", 1, growth_scenario())
        with crash_after_commit():
            with self.assertRaises(OSError):
                ds.issue("R", "S", 1)  # committed, but the caller never received it
        ds.close()
        reopened = self.open()
        receipt = reopened.get_receipt("R")  # the id and its receipt committed together
        self.assertEqual((receipt["scenario_id"], receipt["revision"]), ("S", 1))
        retry = reopened.issue("R", "S", 1)  # retrying the same request must not re-issue
        self.assertEqual(retry, receipt)
        self.assertEqual(reopened.consume("R")["revision"], 1)
        with self.assertRaises(ValueError):
            reopened.issue("R", "S", 2)  # and the id still cannot be rebound
        reopened.close()

    def test_crash_before_put_commit_keeps_old_revision_live(self):
        ds = self.open()
        ds.put_scenario("S", 1, growth_scenario())
        ds.issue("R", "S", 1)
        with crash_before_commit():
            with self.assertRaises(OSError):
                ds.put_scenario("S", 2, growth_scenario())
        self.assertEqual(ds.consume("R")["revision"], 1)  # nothing half-committed
        ds.close()
        reopened = self.open()
        self.assertEqual(reopened.consume("R")["revision"], 1)
        reopened.put_scenario("S", 2, growth_scenario())  # the same revision can be retried
        with self.assertRaises(ValueError):
            reopened.consume("R")
        reopened.close()

    def test_committed_put_revokes_old_receipt_atomically(self):
        ds = self.open()
        ds.put_scenario("S", 1, growth_scenario())
        ds.issue("R", "S", 1)
        with crash_after_commit():
            with self.assertRaises(OSError):
                ds.put_scenario("S", 2, growth_scenario())  # committed, response lost
        ds.close()
        reopened = self.open()
        # the new current revision and the revocation of the old receipt are one commit
        with self.assertRaises(ValueError):
            reopened.consume("R")
        self.assertEqual(reopened.get_receipt("R")["revision"], 1)  # audit trail preserved
        with self.assertRaises(ValueError):
            reopened.put_scenario("S", 2, growth_scenario())  # revision 2 already committed
        reopened.put_scenario("S", 3, growth_scenario())
        reopened.close()

    def test_stale_temp_files_do_not_affect_reopen(self):
        ds = self.open()
        ds.put_scenario("S", 1, growth_scenario())
        with crash_before_commit():
            with self.assertRaises(OSError):
                ds.issue("R", "S", 1)
        leftover = self.path.parent / "dataset.json.orphaned.tmp"
        leftover.write_text("{not json", encoding="utf-8")  # debris from a dead process
        ds.close()
        reopened = self.open()
        with self.assertRaises(ValueError):
            reopened.get_receipt("R")
        self.assertTrue(reopened.put_scenario("T", 1, growth_scenario())["eligible"])
        reopened.close()


if __name__ == "__main__":
    unittest.main()
