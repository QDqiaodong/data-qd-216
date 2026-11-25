import unittest
from load_balance import process


class LoadBalanceTests(unittest.TestCase):
    def base(self):
        return {"stations":[{"id":"nose","arm_m":"1","max_weight_kg":"100"},{"id":"cabin","arm_m":"3","max_weight_kg":"400"}],"loads":[{"id":"crew","station_id":"nose","weight_kg":"100"},{"id":"cargo","station_id":"cabin","weight_kg":"300"}],"envelope":[{"weight_kg":"300","forward_cg_m":"2","aft_cg_m":"3"},{"weight_kg":"500","forward_cg_m":"2.2","aft_cg_m":"2.8"}]}
    def test_moment_and_interpolated_limits(self):
        result=process(self.base())
        self.assertEqual((result["cg_m"],result["forward_limit_m"],result["aft_limit_m"]),("5/2","21/10","29/10"))
        self.assertTrue(result["dispatchable"])
    def test_station_overload_blocks_dispatch(self):
        data=self.base(); data["stations"][1]["max_weight_kg"]="250"
        self.assertFalse(process(data)["dispatchable"])
    def test_forward_limit(self):
        data=self.base(); data["loads"][1]["weight_kg"]="100"
        self.assertEqual(process(data)["envelope_status"],"weight_outside_envelope")
    def test_aft_limit(self):
        data=self.base(); data["loads"][0]["weight_kg"]="10"; data["loads"][1]["weight_kg"]="390"
        self.assertEqual(process(data)["envelope_status"],"aft_of_limit")
    def test_unknown_station_fails(self):
        data=self.base(); data["loads"][0]["station_id"]="missing"
        with self.assertRaises(ValueError): process(data)
    def test_envelope_weights_must_increase(self):
        data=self.base(); data["envelope"][1]["weight_kg"]="300"
        with self.assertRaises(ValueError): process(data)

if __name__ == "__main__": unittest.main()
