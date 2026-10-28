import hashlib, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "l2"))
import netfile_transactions as nt


def tx(i, filing, name, date, amount, occupation="teacher", city="Berkeley"):
    return {"id": i, "filingId": filing, "filerLocalId": "BRK-1", "filerName": "Committee A", "date": date + "T00:00:00",
            "amount": amount, "transactionType": 0, "name": name, "occupation": occupation, "city": city, "state": "CA"}


class TestNetfileL2(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "manifest"))
        os.makedirs(os.path.join(self.root, "L0"))

    def put(self, mid, obj):
        raw = json.dumps(obj).encode()
        path = os.path.join("L0", mid + ".json")
        with open(os.path.join(self.root, path), "wb") as f:
            f.write(raw)
        with open(os.path.join(self.root, "manifest", mid + ".json"), "w") as f:
            json.dump({"id": mid, "file": path, "sha256": hashlib.sha256(raw).hexdigest()}, f)

    def recipe(self):
        rows = [tx("a", "100", "Pat Doe", "2026-01-05", 50.0), tx("b", "101", "Pat Doe", "2026-01-05", 50.0),  # 497 + 460
                tx("c", "100", "Local 3 PAC", "2026-01-06", 500.0, occupation=""),
                tx("d", "900", "Late Donor", "2026-10-20", 25.0)]                                               # late filing
        self.put("w1", {"totalMatchingCount": 4, "results": rows})
        self.put("info900", {"filingId": "900", "filingDate": "2026-10-22T10:15:05.4130000-07:00", "amends": "800"})
        self.put("sup800", {"totalMatchingCount": 1, "results": [tx("e", "800", "Orig Donor", "2026-10-01", 10.0)]})
        return {"id": "x", "file": "L2/x.csv", "cutoff": "2026-10-22T08:10:00-07:00", "windows": ["w1"],
                "excluded_filing_info": ["info900"], "superseded": [{"filingId": "800", "response": "sup800"}]}

    def test_build(self):
        data, n = nt.build(self.recipe(), self.root)
        lines = data.decode().splitlines()
        self.assertEqual(n, 3)                                       # 497/460 pair collapsed; late row out; original in
        self.assertEqual(lines[0].split(","), nt.COLUMNS)
        self.assertNotIn("Late Donor", data.decode())
        self.assertNotIn("Pat Doe", data.decode())                   # names never written
        self.assertTrue(any(",1,0,0" in l for l in lines))           # the PAC is flagged org-like
        self.assertEqual(nt.build(self.recipe(), self.root)[0], data)  # deterministic

    def test_same_amount_on_two_schedules_kept(self):
        r = self.recipe()
        a = tx("s1", "300", "Pat Roe", "2017-12-27", 3883.1)
        b = dict(tx("s2", "300", "Pat Roe", "2017-12-27", 3883.1), transactionType=2)   # Schedule I line, same figures
        self.put("w1", {"totalMatchingCount": 2, "results": [a, b]})
        r["excluded_filing_info"], r["superseded"] = [], []
        data, n = nt.build(r, self.root)
        self.assertEqual(n, 2)

    def test_refuses_paged_window_and_early_exclusion(self):
        r = self.recipe()
        self.put("w1", {"totalMatchingCount": 5, "results": []})
        with self.assertRaisesRegex(SystemExit, "paged"):
            nt.build(r, self.root)
        r = self.recipe()
        self.put("info900", {"filingId": "900", "filingDate": "2026-10-22T07:00:00-07:00", "amends": "800"})
        with self.assertRaisesRegex(SystemExit, "not after the cutoff"):
            nt.build(r, self.root)


if __name__ == "__main__":
    unittest.main()
