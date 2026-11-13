"""Regression tests for verdict correctness (unknowns, VRAM, DirectX, laptop modifiers)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from specbench import compare, dx, matcher, requirements, specs  # noqa: E402


def machine(**kw):
    gpu = specs.describe_gpu("NVIDIA GeForce RTX 3070", 8.0)
    gpu["directx"] = {"directx": 12, "feature_level": "12_2", "source": "detected"}
    base = {"cpu": specs.describe_cpu("AMD Ryzen 7 5800X"), "gpu": gpu, "ram_gb": 32,
            "os": {"name": "Windows 11", "version": 11.0, "is_64bit": True},
            "best_drive": {"id": "C:", "free_gb": 500.0}}
    base.update(kw)
    return base


def steam(**fields):
    labels = {"os": "OS", "cpu": "Processor", "ram": "Memory", "gpu": "Graphics",
              "directx": "DirectX", "storage": "Storage"}
    items = "".join("<li><strong>%s:</strong> %s<br></li>" % (labels[k], v) for k, v in fields.items())
    return requirements.analyze(requirements.parse_steam("<strong>Minimum:</strong><br><ul>%s</ul>" % items))


def statuses(res):
    return {r["key"]: r["status"] for r in res["rows"]}


class UnknownRequirements(unittest.TestCase):
    def test_unrecognised_cpu_and_gpu_do_not_give_success(self):
        req = steam(os="Windows 10", cpu="Quantum Flux 9000 Hyperthreader",
                    gpu="A graphics card from a respectable vendor", ram="8 GB RAM")
        res = compare.compare(machine(), req)
        self.assertEqual(statuses(res)["ram"], "pass")
        self.assertEqual(res["verdict"], "partial")
        self.assertEqual(sorted(res["unchecked"]), ["Graphics", "Processor"])
        summary = compare.summarize({"minimum": res})
        self.assertEqual(summary["tier"], "partial")
        self.assertEqual(summary["group"], "incomplete")
        self.assertNotIn("Meets", summary["headline"])

    def test_definite_failure_survives_unknowns(self):
        req = steam(cpu="Quantum Flux 9000", gpu="Mystery card", ram="64 GB RAM")
        res = compare.compare(machine(ram_gb=8), req)
        self.assertEqual(res["verdict"], "fail")
        self.assertEqual(compare.summarize({"minimum": res})["tier"], "fail")

    def test_missing_is_distinct_from_unreadable(self):
        req = steam(os="Windows 10", gpu="GeForce GTX 1060", ram="8 GB RAM")
        res = compare.compare(machine(), req)
        cpu = next(r for r in res["rows"] if r["key"] == "cpu")
        self.assertEqual(cpu["unknown_reason"], "not_listed")
        self.assertEqual(res["verdict"], "pass")
        self.assertEqual(res["missing"], ["Processor"])
        summary = compare.summarize({"minimum": res})
        self.assertEqual(summary["tier"], "ok")
        self.assertIn("does not name a processor", summary["detail"])

        req = steam(os="Windows 10", cpu="Quantum Flux 9000", gpu="GeForce GTX 1060", ram="8 GB RAM")
        cpu = next(r for r in compare.compare(machine(), req)["rows"] if r["key"] == "cpu")
        self.assertEqual(cpu["unknown_reason"], "unreadable")

    def test_local_part_unknown_is_partial(self):
        mine = machine(cpu=specs.describe_cpu("Totally Unknown Chip"))
        res = compare.compare(mine, steam(cpu="Core i5-8400", gpu="GTX 1060", ram="8 GB RAM"))
        cpu = next(r for r in res["rows"] if r["key"] == "cpu")
        self.assertEqual(cpu["unknown_reason"], "local_unknown")
        self.assertEqual(res["verdict"], "partial")

    def test_only_ram_known_is_not_unknown_if_gpu_fails(self):
        weak = machine(gpu=specs.describe_gpu("Intel HD Graphics 4000"))
        res = compare.compare(weak, steam(cpu="Unknown CPU thing", gpu="GeForce RTX 3080", ram="8 GB"))
        self.assertEqual(res["verdict"], "fail")


class VideoMemory(unittest.TestCase):
    def test_steam_vram_in_graphics_line_is_checked(self):
        req = steam(cpu="Core i5-8400", gpu="GeForce GTX 1060, 6 GB VRAM", ram="8 GB RAM")
        self.assertEqual(req["vram_gb"], 6)
        mine = machine(gpu=dict(specs.describe_gpu("NVIDIA GeForce GTX 1080", 4.0),
                                directx={"directx": 12, "source": "detected"}))
        res = compare.compare(mine, req)
        self.assertIn("vram", statuses(res))
        self.assertEqual(statuses(res)["vram"], "fail")

    def test_severe_shortfall_fails_and_small_one_is_close(self):
        req = steam(cpu="Core i5-8400", gpu="GeForce GTX 1060, 8 GB VRAM", ram="8 GB RAM")
        for have, want in ((1.0, "fail"), (4.0, "fail"), (6.0, "close"), (8.0, "pass")):
            gpu = dict(specs.describe_gpu("NVIDIA GeForce RTX 4090", have),
                       directx={"directx": 12, "source": "detected"})
            with self.subTest(have=have):
                res = compare.compare(machine(gpu=gpu), req)
                self.assertEqual(statuses(res)["vram"], want)
        gpu = dict(specs.describe_gpu("NVIDIA GeForce RTX 4090", 1.0),
                   directx={"directx": 12, "source": "detected"})
        res = compare.compare(machine(gpu=gpu), req)
        self.assertEqual(res["verdict"], "fail")
        self.assertNotIn("should start", compare.summarize({"minimum": res})["detail"])

    def test_unknown_vram_is_shown_as_uncertain(self):
        req = steam(cpu="Core i5-8400", gpu="GeForce GTX 1060, 6 GB VRAM", ram="8 GB RAM")
        gpu = specs.describe_gpu("NVIDIA GeForce RTX 3080")
        gpu["vram_gb"] = None
        res = compare.compare(machine(gpu=gpu), req)
        row = next(r for r in res["rows"] if r["key"] == "vram")
        self.assertEqual((row["status"], row["unknown_reason"]), ("unknown", "local_unknown"))
        self.assertEqual(res["verdict"], "partial")

    def test_pcgw_vram_field_still_works(self):
        req = requirements.analyze({"fields": {"cpu": "Core i5-8400", "gpu": "GTX 1060",
                                               "ram": "8 GB", "vram": "4 GB"}})
        res = compare.compare(machine(), req)
        self.assertEqual(statuses(res)["vram"], "pass")


class DirectX(unittest.TestCase):
    def test_windows_version_alone_does_not_give_directx(self):
        gpu = specs.describe_gpu("Mystery Graphics Adapter 3000", 4.0)
        self.assertIsNone(gpu["directx"])
        req = steam(cpu="Core i5-8400", gpu="GeForce GTX 1060", ram="8 GB", directx="Version 12")
        res = compare.compare(machine(gpu=gpu), req)
        row = next(r for r in res["rows"] if r["key"] == "directx")
        self.assertEqual(row["status"], "unknown")

    def test_detected_feature_level_is_used(self):
        old = specs.describe_gpu("NVIDIA GeForce RTX 3070", 8.0)
        old["directx"] = {"directx": 11, "feature_level": "11_0", "source": "detected"}
        req = steam(cpu="Core i5-8400", gpu="GeForce GTX 1060", ram="8 GB", directx="Version 12")
        res = compare.compare(machine(gpu=old), req)
        self.assertEqual(statuses(res)["directx"], "fail")

    def test_hypothetical_card_is_estimated_from_model(self):
        sim = specs.with_overrides(machine(adapters=[]), gpu="GeForce RTX 4070")
        self.assertEqual(sim["gpu"]["directx"]["source"], "model")
        self.assertEqual(sim["directx"], 12)
        self.assertTrue(sim["simulated"])
        req = steam(cpu="Core i5-8400", gpu="GeForce GTX 1060", ram="8 GB", directx="Version 12")
        row = next(r for r in compare.compare(sim, req)["rows"] if r["key"] == "directx")
        self.assertEqual(row["status"], "pass")
        self.assertTrue(row["estimated"])

    def test_model_rules(self):
        cases = {"GeForce GTX 1060": 12, "Radeon RX 580": 12, "Radeon HD 6870": 11,
                 "HD 4000": 11, "Intel(R) UHD Graphics 630": 12, "GeForce 8800 GT": 10,
                 "AMD Radeon(TM) Graphics": 12, "Radeon HD 6450 Graphics": 11}
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(dx.estimate_from_model(name)["directx"], want)
        self.assertIsNone(dx.estimate_from_model("Matrox G200"))

    def test_levels(self):
        self.assertEqual(dx.directx_from_levels(dx.FL["12_1"], dx.FL["11_1"]), 12)
        self.assertEqual(dx.directx_from_levels(None, dx.FL["11_0"]), 11)
        self.assertEqual(dx.directx_from_levels(None, dx.FL["10_1"]), 10)
        self.assertIsNone(dx.directx_from_levels(None, None))

    def test_probe_matches_adapters_by_name(self):
        adapters = [dict(specs.describe_gpu("NVIDIA GeForce RTX 3070"), virtual=False),
                    dict(specs.describe_gpu("Intel(R) UHD Graphics 630"), virtual=False)]
        probed = [{"name": "NVIDIA GeForce RTX 3070", "dedicated_gb": 8.0, "shared_gb": 16.0,
                   "software": False, "d3d12": True, "directx": 12, "feature_level": "12_2"},
                  {"name": "Microsoft Basic Render Driver", "dedicated_gb": 0, "shared_gb": 16.0,
                   "software": True, "d3d12": True, "directx": 12, "feature_level": "12_1"}]
        specs._apply_probe(adapters, probed)
        self.assertEqual(adapters[0]["directx"]["source"], "detected")
        self.assertEqual(adapters[0]["vram_gb"], 8.0)
        self.assertEqual(adapters[1]["directx"]["source"], "model")


class LaptopModifiers(unittest.TestCase):
    def test_laptop_word_only_affects_its_own_card(self):
        cands = matcher.match_gpus("GeForce GTX 1060 Mobile or Radeon RX 580")
        self.assertEqual([c["kind"] for c in cands], ["variant", "exact"])
        cands = matcher.match_gpus("GeForce GTX 1070 / GeForce RTX 3060 Laptop GPU")
        self.assertEqual([c["kind"] for c in cands], ["exact", "variant"])
        self.assertEqual(cands[0]["name"], "GeForce GTX 1070")

    def test_prefix_word(self):
        cands = matcher.match_gpus("Laptop: GTX 1650, desktop: GTX 1060")
        self.assertEqual([c["kind"] for c in cands], ["variant", "exact"])
        cands = matcher.match_gpus("Mobile GTX 1060 or desktop GTX 970")
        self.assertEqual([c["kind"] for c in cands], ["variant", "exact"])

    def test_local_laptop_adapter(self):
        self.assertEqual(matcher.identify_gpu("NVIDIA GeForce RTX 3070 Ti Laptop GPU")["kind"], "variant")
        self.assertEqual(matcher.identify_gpu("NVIDIA GeForce RTX 3070 Max-Q")["kind"], "variant")


class Wording(unittest.TestCase):
    def test_headlines_do_not_promise_performance(self):
        req = steam(os="Windows 10", cpu="Core i5-2500K", gpu="GeForce GTX 780", ram="8 GB RAM")
        summary = compare.summarize({"minimum": compare.compare(machine(), req)})
        for word in ("smooth", "runs well", "fps", "settings"):
            self.assertNotIn(word, summary["headline"].lower())

    def test_mentions_are_listed_as_not_checked(self):
        req = requirements.analyze({"fields": {"cpu": "Core i5-8400 (AVX2 required)", "gpu": "GTX 1060",
                                               "ram": "8 GB", "storage": "50 GB SSD"}})
        res = compare.compare(machine(), req)
        self.assertIn("AVX2 instructions", res["not_checked"])
        self.assertIn("an SSD", res["not_checked"])

    def test_disagreements(self):
        mine = machine()
        a = {"minimum": compare.compare(mine, steam(cpu="Core i5-8400", gpu="GTX 1060", ram="8 GB"))}
        b = {"minimum": compare.compare(mine, steam(cpu="Core i5-8400", gpu="RTX 3080", ram="16 GB"))}
        notes = compare.disagreements(a, b)
        self.assertTrue(any("memory" in n for n in notes))
        self.assertTrue(any("graphics" in n for n in notes))
        self.assertEqual(compare.disagreements(a, a), [])


if __name__ == "__main__":
    unittest.main()
