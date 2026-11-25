import unittest
from load_balance import process


STATIONS = [
    {"id": "s1", "arm_m": "2", "max_weight_kg": "500"},
    {"id": "s2", "arm_m": "4", "max_weight_kg": "500"},
]


def growth_frames():
    """100 kg at arm 2 m; the next frame adds 100 kg at arm 4 m."""
    return [
        {"loads": [{"id": "a", "station_id": "s1", "weight_kg": "100"}]},
        {"loads": [
            {"id": "a", "station_id": "s1", "weight_kg": "100"},
            {"id": "b", "station_id": "s2", "weight_kg": "100"},
        ]},
    ]


def scenario(envelope, frames, stations=None):
    return {"stations": stations if stations is not None else [dict(row) for row in STATIONS],
            "envelope": envelope, "frames": frames}


def two_point_envelope(aft1, aft2, fwd1="1", fwd2="1"):
    return [
        {"weight_kg": "100", "forward_cg_m": fwd1, "aft_cg_m": aft1},
        {"weight_kg": "200", "forward_cg_m": fwd2, "aft_cg_m": aft2},
    ]


def three_point_envelope(aft1, aft2, aft3):
    return [
        {"weight_kg": "100", "forward_cg_m": "1", "aft_cg_m": aft1},
        {"weight_kg": "150", "forward_cg_m": "1", "aft_cg_m": aft2},
        {"weight_kg": "200", "forward_cg_m": "1", "aft_cg_m": aft3},
    ]


class TrajectoryAnalysisTests(unittest.TestCase):
    def test_frames_match_static_process(self):
        data = scenario(two_point_envelope("2.08", "3.36"), growth_frames())
        analysis = process(data)
        self.assertEqual(set(analysis), {"eligible", "frames", "violations"})
        for frame, result in zip(data["frames"], analysis["frames"]):
            static = {"stations": data["stations"], "envelope": data["envelope"], "loads": frame["loads"]}
            self.assertEqual(result, process(static))

    def test_interior_aft_violation_rejects_otherwise_clean_frames(self):
        data = scenario(two_point_envelope("2", "3"), growth_frames())
        analysis = process(data)
        self.assertTrue(all(frame["dispatchable"] for frame in analysis["frames"]))
        self.assertFalse(analysis["eligible"])
        self.assertEqual(analysis["violations"], [
            {"segment_index": 0, "envelope_reasons": ["aft_of_limit"], "over_limit_station_ids": []},
        ])
        # the midpoint instant: 150 kg -> cg 8/3 against an aft limit of 5/2
        midpoint = {"stations": data["stations"], "envelope": data["envelope"], "loads": [
            {"id": "m1", "station_id": "s1", "weight_kg": "100"},
            {"id": "m2", "station_id": "s2", "weight_kg": "50"},
        ]}
        mid = process(midpoint)
        self.assertEqual((mid["total_weight_kg"], mid["cg_m"], mid["aft_limit_m"]), ("150", "8/3", "5/2"))
        self.assertEqual(mid["envelope_status"], "aft_of_limit")

    def test_tangent_to_aft_limit_is_eligible(self):
        data = scenario(two_point_envelope("2.08", "3.36"), growth_frames())
        analysis = process(data)
        self.assertTrue(analysis["eligible"])
        self.assertEqual(analysis["violations"], [])
        self.assertEqual(analysis["frames"][0]["aft_limit_m"], "52/25")
        self.assertEqual(analysis["frames"][1]["aft_limit_m"], "84/25")
        # tangent instant: 125 kg -> cg 12/5 exactly on the aft limit 12/5
        tangent = {"stations": data["stations"], "envelope": data["envelope"], "loads": [
            {"id": "m1", "station_id": "s1", "weight_kg": "100"},
            {"id": "m2", "station_id": "s2", "weight_kg": "25"},
        ]}
        touch = process(tangent)
        self.assertEqual((touch["total_weight_kg"], touch["cg_m"], touch["aft_limit_m"]), ("125", "12/5", "12/5"))
        self.assertEqual(touch["envelope_status"], "within_envelope")

    def test_crossing_envelope_breakpoint(self):
        clean = process(scenario(three_point_envelope("2.7", "2.75", "3.6"), growth_frames()))
        self.assertTrue(clean["eligible"])
        # the kink at 150 kg dips below the trajectory cg of 8/3 there
        kinked = process(scenario(three_point_envelope("2.6", "2.5", "3.6"), growth_frames()))
        self.assertTrue(all(frame["dispatchable"] for frame in kinked["frames"]))
        self.assertFalse(kinked["eligible"])
        self.assertEqual(kinked["violations"], [
            {"segment_index": 0, "envelope_reasons": ["aft_of_limit"], "over_limit_station_ids": []},
        ])

    def test_constant_total_weight(self):
        frames = [
            {"loads": [{"id": "a", "station_id": "s1", "weight_kg": "150"}]},
            {"loads": [
                {"id": "a", "station_id": "s1", "weight_kg": "75"},
                {"id": "b", "station_id": "s2", "weight_kg": "75"},
            ]},
        ]
        ok = process(scenario(two_point_envelope("2.6", "3.4"), frames))
        self.assertEqual([f["total_weight_kg"] for f in ok["frames"]], ["150", "150"])
        self.assertTrue(ok["eligible"])
        # aft limit at 150 kg is 2.6 here; cg drifts 2 -> 3 and crosses it
        bad = process(scenario(two_point_envelope("2.4", "2.8"), frames))
        self.assertFalse(bad["eligible"])
        self.assertEqual(bad["violations"], [
            {"segment_index": 0, "envelope_reasons": ["aft_of_limit"], "over_limit_station_ids": []},
        ])

    def test_station_over_limit_inside_envelope(self):
        stations = [
            {"id": "s1", "arm_m": "2", "max_weight_kg": "120"},
            {"id": "s2", "arm_m": "4", "max_weight_kg": "500"},
        ]
        frames = [
            {"loads": [{"id": "a", "station_id": "s1", "weight_kg": "100"}]},
            {"loads": [{"id": "a", "station_id": "s1", "weight_kg": "150"}]},
        ]
        analysis = process(scenario(two_point_envelope("10", "10"), frames, stations))
        self.assertEqual([f["envelope_status"] for f in analysis["frames"]], ["within_envelope", "within_envelope"])
        self.assertFalse(analysis["eligible"])
        self.assertEqual(analysis["violations"], [
            {"segment_index": 0, "envelope_reasons": [], "over_limit_station_ids": ["s1"]},
        ])

    def test_shared_endpoint_violation_counts_in_each_adjacent_segment(self):
        stations = [
            {"id": "s1", "arm_m": "2", "max_weight_kg": "120"},
            {"id": "s2", "arm_m": "4", "max_weight_kg": "500"},
        ]
        frames = [
            {"loads": [{"id": "a", "station_id": "s1", "weight_kg": "100"}]},
            {"loads": [{"id": "a", "station_id": "s1", "weight_kg": "150"}]},
            {"loads": [{"id": "a", "station_id": "s1", "weight_kg": "100"}]},
        ]
        analysis = process(scenario(two_point_envelope("10", "10"), frames, stations))
        self.assertEqual(analysis["violations"], [
            {"segment_index": 0, "envelope_reasons": [], "over_limit_station_ids": ["s1"]},
            {"segment_index": 1, "envelope_reasons": [], "over_limit_station_ids": ["s1"]},
        ])

    def test_weight_outside_envelope_and_cg_judged_only_inside(self):
        stations = [
            {"id": "s1", "arm_m": "2", "max_weight_kg": "1000"},
            {"id": "s2", "arm_m": "5", "max_weight_kg": "1000"},
        ]
        frames = [
            {"loads": [{"id": "a", "station_id": "s1", "weight_kg": "250"}]},
            {"loads": [{"id": "b", "station_id": "s2", "weight_kg": "400"}]},
        ]
        envelope = [
            {"weight_kg": "50", "forward_cg_m": "2.2", "aft_cg_m": "4"},
            {"weight_kg": "300", "forward_cg_m": "2.2", "aft_cg_m": "4"},
        ]
        analysis = process(scenario(envelope, frames, stations))
        # cg passes the aft limit 4 only above 300 kg, i.e. outside the envelope: not reported
        self.assertEqual(analysis["violations"], [
            {"segment_index": 0,
             "envelope_reasons": ["weight_outside_envelope", "forward_of_limit"],
             "over_limit_station_ids": []},
        ])
        self.assertEqual(analysis["frames"][1]["envelope_status"], "weight_outside_envelope")
        self.assertIsNone(analysis["frames"][1]["forward_limit_m"])
        # loose limits: only the weight range is reported
        loose = [dict(row, forward_cg_m="1", aft_cg_m="10") for row in envelope]
        pure = process(scenario(loose, frames, stations))
        self.assertEqual(pure["violations"], [
            {"segment_index": 0, "envelope_reasons": ["weight_outside_envelope"], "over_limit_station_ids": []},
        ])

    def test_forward_of_limit_at_envelope_kink(self):
        frames = [
            {"loads": [{"id": "a", "station_id": "s2", "weight_kg": "100"}]},
            {"loads": [
                {"id": "a", "station_id": "s2", "weight_kg": "100"},
                {"id": "b", "station_id": "s1", "weight_kg": "100"},
            ]},
        ]
        envelope = [
            {"weight_kg": "100", "forward_cg_m": "2", "aft_cg_m": "5"},
            {"weight_kg": "150", "forward_cg_m": "3.5", "aft_cg_m": "5"},
            {"weight_kg": "200", "forward_cg_m": "2", "aft_cg_m": "5"},
        ]
        analysis = process(scenario(envelope, frames))
        self.assertTrue(all(frame["dispatchable"] for frame in analysis["frames"]))
        self.assertEqual(analysis["violations"], [
            {"segment_index": 0, "envelope_reasons": ["forward_of_limit"], "over_limit_station_ids": []},
        ])

    def test_over_limit_station_ids_sorted_as_strings(self):
        stations = [
            {"id": "zeta", "arm_m": "2", "max_weight_kg": "10"},
            {"id": "alpha", "arm_m": "4", "max_weight_kg": "10"},
        ]
        loads = [
            {"id": "a", "station_id": "zeta", "weight_kg": "50"},
            {"id": "b", "station_id": "alpha", "weight_kg": "60"},
        ]
        frames = [{"loads": [dict(row) for row in loads]}, {"loads": [dict(row) for row in loads]}]
        analysis = process(scenario(two_point_envelope("10", "10"), frames, stations))
        self.assertEqual(analysis["violations"], [
            {"segment_index": 0, "envelope_reasons": [], "over_limit_station_ids": ["alpha", "zeta"]},
        ])


class TrajectoryValidationTests(unittest.TestCase):
    def valid(self):
        return scenario(two_point_envelope("2.08", "3.36"), growth_frames())

    def test_frame_count_bounds(self):
        data = self.valid(); data["frames"] = data["frames"][:1]
        with self.assertRaises(ValueError): process(data)
        data = self.valid(); data["frames"] = [data["frames"][0]] * 21
        with self.assertRaises(ValueError): process(data)
        data = self.valid(); data["frames"] = [data["frames"][0]] * 20
        self.assertTrue(process(data)["eligible"])
        data = self.valid(); data["frames"] = "not-a-list"
        with self.assertRaises(ValueError): process(data)

    def test_frame_loads_follow_static_rules(self):
        data = self.valid(); data["frames"][0] = {"loads": []}
        with self.assertRaises(ValueError): process(data)
        data = self.valid(); data["frames"][0] = {}
        with self.assertRaises(ValueError): process(data)
        data = self.valid(); data["frames"][0] = "not-a-dict"
        with self.assertRaises(ValueError): process(data)
        data = self.valid()
        data["frames"][0]["loads"].append({"id": "a", "station_id": "s1", "weight_kg": "10"})
        with self.assertRaises(ValueError): process(data)  # duplicate id within one frame
        data = self.valid(); data["frames"][0]["loads"][0]["station_id"] = "nowhere"
        with self.assertRaises(ValueError): process(data)
        data = self.valid(); data["frames"][0]["loads"][0]["weight_kg"] = "abc"
        with self.assertRaises(ValueError): process(data)
        data = self.valid(); data["frames"][0]["loads"][0]["weight_kg"] = "-5"
        with self.assertRaises(ValueError): process(data)

    def test_load_ids_may_repeat_across_frames(self):
        self.assertTrue(process(self.valid())["eligible"])  # id "a" appears in both frames

    def test_shared_structure_rules(self):
        data = self.valid(); data["envelope"][1]["weight_kg"] = "100"
        with self.assertRaises(ValueError): process(data)  # weights must increase
        data = self.valid(); data["envelope"][0]["forward_cg_m"] = "9"
        with self.assertRaises(ValueError): process(data)  # forward beyond aft
        data = self.valid(); data["stations"].append({"id": "s1", "arm_m": "1", "max_weight_kg": "1"})
        with self.assertRaises(ValueError): process(data)  # duplicate station id
        data = self.valid(); data["stations"] = []
        with self.assertRaises(ValueError): process(data)

    def test_static_input_keeps_static_shape(self):
        result = process({
            "stations": [dict(row) for row in STATIONS],
            "loads": [{"id": "a", "station_id": "s1", "weight_kg": "100"}],
            "envelope": two_point_envelope("2.08", "3.36"),
        })
        self.assertIn("dispatchable", result)
        self.assertNotIn("eligible", result)
        self.assertNotIn("violations", result)


if __name__ == "__main__":
    unittest.main()
