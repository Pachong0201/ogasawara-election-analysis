import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

from runtime.cec_open_data import CECOpenDataAdapter
from runtime.election_loader import ElectionLoader, election_file_path, load_jsonl
from runtime.models import DataQuery
from runtime.source_registry import SourceRegistry


def csv_bytes(rows):
    text = "\n".join(",".join(str(value) for value in row) for row in rows) + "\n"
    return text.encode("utf-8-sig")


def candidate_row(prov, county, district, number, name, party_code, vice=""):
    return [
        prov, county, district, "000", "0000",
        number, name, party_code, "1", "0700101", "50", "臺灣省", "大學", "N", "", vice,
    ]


def profile_row(prov, county, district, town, valid, invalid, electors):
    total = valid + invalid
    return [
        prov, county, district, town, "0000", "0000",
        valid, invalid, total, electors, 0,
        0, 0, 0, 0, 0, 0,
        0, round(total / electors * 100, 2), 0,
    ]


def vote_row(prov, county, district, town, candidate_no, votes):
    return [prov, county, district, town, "0000", "0000", candidate_no, votes, 0, ""]


def base_rows(district="00"):
    return [
        ["10", "002", "00", "000", "0000", "宜蘭縣"],
        ["10", "002", district, "001", "0000", "宜蘭市"],
        ["10", "002", district, "002", "0000", "羅東鎮"],
    ]


def party_rows():
    return [["1", "中國國民黨"], ["16", "民主進步黨"]]


def add_family(zf, folder, suffix, candidates, profiles, votes, district="00"):
    prefix = f"votedata/votedata/voteData/{folder}"
    zf.writestr(f"{prefix}/elbase{suffix}.csv", csv_bytes(base_rows(district)))
    zf.writestr(f"{prefix}/elcand{suffix}.csv", csv_bytes(candidates))
    zf.writestr(f"{prefix}/elpaty.csv", csv_bytes(party_rows()))
    zf.writestr(f"{prefix}/elprof{suffix}.csv", csv_bytes(profiles))
    zf.writestr(f"{prefix}/elctks{suffix}.csv", csv_bytes(votes))


def build_archive(path: Path):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 2024 president: two townships, two presidential tickets.
        pres_candidates = [
            candidate_row("00", "000", "00", "1", "甲總統", "1"),
            candidate_row("00", "000", "00", "1", "甲副總統", "1", vice="Y"),
            candidate_row("00", "000", "00", "2", "乙總統", "16"),
            candidate_row("00", "000", "00", "2", "乙副總統", "16", vice="Y"),
        ]
        pres_profiles = [
            profile_row("10", "002", "00", "001", 1000, 20, 1500),
            profile_row("10", "002", "00", "002", 800, 20, 1200),
        ]
        pres_votes = [
            vote_row("10", "002", "00", "001", "1", 400),
            vote_row("10", "002", "00", "001", "2", 600),
            vote_row("10", "002", "00", "002", "1", 350),
            vote_row("10", "002", "00", "002", "2", 450),
        ]
        add_family(
            zf,
            "2024總統立委/總統",
            "",
            pres_candidates,
            pres_profiles,
            pres_votes,
        )

        # 2016 regional legislator: official archive uses _T1 filename suffix.
        leg_candidates = [
            candidate_row("10", "002", "01", "1", "甲立委", "1"),
            candidate_row("10", "002", "01", "2", "乙立委", "16"),
        ]
        leg_profiles = [
            profile_row("10", "002", "01", "001", 900, 20, 1400),
            profile_row("10", "002", "01", "002", 700, 20, 1100),
        ]
        leg_votes = [
            vote_row("10", "002", "01", "001", "1", 420),
            vote_row("10", "002", "01", "001", "2", 480),
            vote_row("10", "002", "01", "002", "1", 320),
            vote_row("10", "002", "01", "002", "2", 380),
        ]
        add_family(
            zf,
            "2016總統立委/區域立委",
            "_T1",
            leg_candidates,
            leg_profiles,
            leg_votes,
            district="01",
        )

        # 2022 county/city mayor in the C1/city path.
        mayor_candidates = [
            candidate_row("10", "002", "00", "1", "甲縣長", "1"),
            candidate_row("10", "002", "00", "2", "乙縣長", "16"),
        ]
        mayor_profiles = [
            profile_row("10", "002", "00", "001", 950, 25, 1450),
            profile_row("10", "002", "00", "002", 750, 25, 1150),
        ]
        mayor_votes = [
            vote_row("10", "002", "00", "001", "1", 500),
            vote_row("10", "002", "00", "001", "2", 450),
            vote_row("10", "002", "00", "002", "1", 360),
            vote_row("10", "002", "00", "002", "2", 390),
        ]
        add_family(
            zf,
            "2022-111年地方公職人員選舉/C1/city",
            "",
            mayor_candidates,
            mayor_profiles,
            mayor_votes,
        )

        # 2022 Chiayi City mayor rerun uses special cand.csv + prof.csv.
        special_prefix = "votedata/votedata/2022年_嘉義市長重行選舉"
        zf.writestr(
            f"{special_prefix}/cand.csv",
            csv_bytes([
                ["號次", "名字", "政黨名稱"],
                ["1", "黃候選人", "中國國民黨"],
                ["2", "李候選人", "民主進步黨"],
            ]),
        )
        zf.writestr(
            f"{special_prefix}/prof.csv",
            csv_bytes([
                [
                    "行政區別", "村里別", "號次1", "號次2",
                    "有效票數A（A＝1＋2＋…＋N）", "無效票數B",
                    "投票數C（C＝A＋B）", "選舉人數(原領票數)G（G＝E＋F）",
                ],
                ["東區", "甲里", "100", "80", "180", "2", "182", "300"],
                ["東區", "乙里", "120", "100", "220", "3", "223", "350"],
                ["西區", "丙里", "110", "90", "200", "2", "202", "320"],
                ["西區", "丁里", "130", "120", "250", "3", "253", "380"],
            ]),
        )



class TestCECOpenDataAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.archive_path = self.root / "votedata.zip"
        build_archive(self.archive_path)

    def tearDown(self):
        self.tmp.cleanup()

    def adapter(self):
        return CECOpenDataAdapter(
            cache_dir=self.root / "cache",
            archive_path=self.archive_path,
        )

    def test_2024_president_parses_simplified_jurisdiction(self):
        result = self.adapter().fetch(
            DataQuery("president", 2024, "宜兰县", "township_district")
        )
        self.assertEqual(len(result.records), 4)
        self.assertEqual({r["jurisdiction"] for r in result.records}, {"宜蘭市", "羅東鎮"})
        self.assertEqual({r["official_parent_jurisdiction"] for r in result.records}, {"宜蘭縣"})
        self.assertEqual({r["parent_jurisdiction"] for r in result.records}, {"宜兰县"})
        self.assertEqual({r["source_grade"] for r in result.records}, {"A"})

        archive_token = hashlib.sha256(self.archive_path.read_bytes()).hexdigest()[:12]
        self.assertEqual(
            {r["boundary_version"] for r in result.records},
            {"cec-township-2014-2024-v1"},
        )
        self.assertEqual(
            {r["source_geography_version"] for r in result.records},
            {f"cec-president-2024-{archive_token}"},
        )

        city = [r for r in result.records if r["jurisdiction"] == "宜蘭市"]
        self.assertEqual(sum(r["votes"] for r in city), 1000)
        self.assertTrue(all(r["valid_votes"] == 1000 for r in city))
        self.assertAlmostEqual(city[0]["turnout"], 1020 / 1500, places=7)

    def test_2016_legislator_discovers_t1_suffix(self):
        result = self.adapter().fetch(
            DataQuery("regional_legislator", 2016, "宜兰县", "township_district")
        )
        self.assertEqual(len(result.records), 4)
        self.assertIn("elctks_T1.csv", result.raw_reference)
        self.assertEqual({r["candidate_name"] for r in result.records}, {"甲立委", "乙立委"})

    def test_2022_county_mayor_c1_city_path(self):
        result = self.adapter().fetch(
            DataQuery("county_mayor", 2022, "宜兰县", "township_district")
        )
        self.assertEqual(len(result.records), 4)
        self.assertEqual({r["candidate_name"] for r in result.records}, {"甲縣長", "乙縣長"})
        self.assertTrue(any("C1/city" in r["raw_reference"] for r in result.records))

    def test_2022_chiayi_city_rerun_special_csvs(self):
        result = self.adapter().fetch(
            DataQuery("county_mayor", 2022, "嘉义市", "township_district")
        )
        self.assertEqual(len(result.records), 4)
        self.assertEqual({r["jurisdiction"] for r in result.records}, {"東區", "西區"})
        self.assertEqual({r["candidate_name"] for r in result.records}, {"黃候選人", "李候選人"})
        self.assertTrue(all(r["special_election"] for r in result.records))
        self.assertEqual({r["election_date"] for r in result.records}, {"2022-12-18"})

        east = [r for r in result.records if r["jurisdiction"] == "東區"]
        west = [r for r in result.records if r["jurisdiction"] == "西區"]
        self.assertEqual(sum(r["votes"] for r in east), 400)
        self.assertTrue(all(r["valid_votes"] == 400 for r in east))
        self.assertAlmostEqual(east[0]["turnout"], 405 / 650, places=7)
        self.assertEqual(sum(r["votes"] for r in west), 450)
        self.assertTrue(all(r["valid_votes"] == 450 for r in west))
        self.assertAlmostEqual(west[0]["turnout"], 455 / 700, places=7)
        self.assertIn("2022年_嘉義市長重行選舉/prof.csv", result.raw_reference)

    def test_archive_cache_reused_without_network(self):
        calls = []

        def fail_opener(url):
            calls.append(url)
            raise AssertionError("network should not be called when archive already exists")

        adapter = CECOpenDataAdapter(
            cache_dir=self.root / "cache",
            archive_path=self.archive_path,
            opener=fail_opener,
        )
        result = adapter.fetch(DataQuery("president", 2024, "宜兰县", "township_district"))
        self.assertEqual(len(result.records), 4)
        self.assertEqual(calls, [])

    def test_loader_persists_election_and_official_geography(self):
        data_root = self.root / "runtime-root"
        registry = SourceRegistry(adapters=[self.adapter()])
        loader = ElectionLoader(data_root, source_registry=registry, mode="online")

        result = loader.load_election(
            "president", 2024, "宜兰县", "township_district"
        )
        self.assertEqual(result.status, "filled")
        self.assertTrue(result.persisted)

        election_path = election_file_path(data_root, "president", 2024, "宜兰县")
        persisted = load_jsonl(election_path)
        self.assertEqual(len(persisted), 4)

        geo_path = (
            data_root
            / "data"
            / "geography"
            / "administrative_areas"
            / "cec_宜兰县.yaml"
        )
        self.assertTrue(geo_path.exists())
        geography = yaml.safe_load(geo_path.read_text(encoding="utf-8"))
        names = {item["name"] for item in geography["regions"]}
        self.assertTrue({"宜兰县", "宜蘭市", "羅東鎮"}.issubset(names))

    def test_real_config_auto_registers_cec_adapter(self):
        repo_root = Path(__file__).resolve().parents[1]
        registry = SourceRegistry(repo_root / "config" / "data_sources.yaml")
        ids = [item["source_id"] for item in registry.metadata()["registered_adapters"]]
        self.assertIn("cec_open_data", ids)


if __name__ == "__main__":
    unittest.main()
