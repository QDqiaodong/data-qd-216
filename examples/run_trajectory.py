"""End-to-end walkthrough of the trajectory sample library on synthetic data.

Run from the repository root:  PYTHONPATH=src python3 examples/run_trajectory.py
"""
import json
import tempfile
from pathlib import Path

from load_balance import TrajectoryDataset, process


def growth_scenario(aft1="2.08", aft2="3.36"):
    """100 kg at arm 2 m; the next frame adds 100 kg at arm 4 m."""
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


def main():
    # with aft limits 2/3 the segment violates mid-way even though both frames pass
    bad = process(growth_scenario(aft1="2", aft2="3"))
    print("mid-segment analysis:", json.dumps(bad["violations"]))

    path = Path(tempfile.mkdtemp(prefix="load-balance-")) / "dataset.json"
    with TrajectoryDataset(path) as ds:
        analysis = ds.put_scenario("demo", 1, growth_scenario())
        print("revision 1 eligible:", analysis["eligible"])

        receipt = ds.issue("REQ-1", "demo", 1)
        print("issued:", receipt["request_id"], "-> revision", receipt["revision"])
        sample = ds.consume("REQ-1")
        print("consumed load ids:", [row["id"] for f in sample["data"]["frames"] for row in f["loads"]])

        # the source corrects the scenario: only load ids change, numbers stay equal
        corrected = growth_scenario()
        corrected["frames"][1]["loads"] = [
            {"id": "a2", "station_id": "s1", "weight_kg": "100"},
            {"id": "b2", "station_id": "s2", "weight_kg": "100"},
        ]
        ds.put_scenario("demo", 2, corrected)
        try:
            ds.consume("REQ-1")
        except ValueError as exc:
            print("REQ-1 revoked:", exc)
        print("audit still returns REQ-1 at revision:", ds.get_receipt("REQ-1")["revision"])
        print("same-binding retry returns revision:", ds.issue("REQ-1", "demo", 1)["revision"])
        ds.issue("REQ-2", "demo", 2)
        print("REQ-2 consumes load ids:", [row["id"] for row in ds.consume("REQ-2")["data"]["frames"][1]["loads"]])

        # an ineligible correction is still accepted as the current revision
        analysis3 = ds.put_scenario("demo", 3, growth_scenario(aft1="2", aft2="3"))
        print("revision 3 eligible:", analysis3["eligible"])
        for request in ("REQ-1", "REQ-2"):
            try:
                ds.consume(request)
            except ValueError as exc:
                print(request, "cannot consume:", exc)

    reopened = TrajectoryDataset(path)  # the same path resumes committed state
    print("after reopen, REQ-1 audit revision:", reopened.get_receipt("REQ-1")["revision"])
    try:
        reopened.consume("REQ-1")
    except ValueError as exc:
        print("after reopen, REQ-1 cannot consume:", exc)
    reopened.close()


if __name__ == "__main__":
    main()
