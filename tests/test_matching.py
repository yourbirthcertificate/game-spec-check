"""Run with:  python -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from specbench import compare, matcher, requirements, specs  # noqa: E402


def names(cands):
    return [c["name"] for c in cands]


class GpuMatching(unittest.TestCase):
    CASES = [
        ("NVIDIA GEFORCE GTX 1060 3 GB or AMD RADEON RX 580 4 GB",
         ["GeForce GTX 1060 3GB", "Radeon RX 580"]),
        ("NVIDIA GeForce RTX 3060 Ti", ["GeForce RTX 3060 Ti"]),
        ("GTX1650 Super / RX570", ["GeForce GTX 1650 Super", "Radeon RX 570"]),
        ("Nvidia GeForce 970 or Radeon 290X", ["GeForce GTX 970", "Radeon R9 290X"]),
        ("AMD RADEON RX VEGA 56 8 GB", ["Radeon RX Vega 56"]),
        ("Intel HD Graphics 4000", ["HD 4000"]),
        ("Intel(R) UHD Graphics 630", ["UHD 630"]),
        ("Intel(R) Iris(R) Xe Graphics", ["Iris Xe"]),
        ("AMD Radeon R9 280X", ["Radeon R9 280X"]),
        ("Radeon HD 7850 2GB", ["Radeon HD 7850"]),
        ("Intel Arc A770", ["Arc A770"]),
        ("GeForce 8800 GT", ["GeForce 8800 GT"]),
        ("NVIDIA GeForce RTX 4080 for 1080p at 144 Hz", ["GeForce RTX 4080"]),
        ("AMD Radeon(TM) Graphics", ["AMD Radeon integrated graphics"]),
    ]

    def test_cases(self):
        for text, want in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(names(matcher.match_gpus(text)), want)

    def test_laptop_chip_is_slower_than_desktop(self):
        laptop = matcher.identify_gpu("NVIDIA GeForce RTX 3070 Ti Laptop GPU")
        desktop = matcher.identify_gpu("NVIDIA GeForce RTX 3070 Ti")
        self.assertLess(laptop["score"], desktop["score"])
        self.assertGreater(laptop["score"], desktop["score"] * 0.6)

    def test_m_suffix(self):
        self.assertLess(matcher.identify_gpu("GeForce GTX 960M")["score"],
                        matcher.identify_gpu("GeForce GTX 960")["score"])

    def test_nonsense_is_not_matched(self):
        self.assertEqual(matcher.match_gpus("OpenGL 3.3 compatible"), [])


class CpuMatching(unittest.TestCase):
    CASES = [
        ("INTEL CORE I5-8400 or AMD RYZEN 3 3300X", ["Core i5-8400", "Ryzen 3 3300X"]),
        ("12th Gen Intel(R) Core(TM) i7-12700H", ["Core i7-12700H"]),
        ("Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz", ["Core i7-8700K"]),
        ("AMD Ryzen 5 5600X 6-Core Processor", ["Ryzen 5 5600X"]),
        ("Intel Core 2 Duo E8400", ["Core 2 Duo E8400"]),
        ("AMD FX-8350", ["FX-8350"]),
        ("AMD Phenom II X4 965", ["Phenom II X4 965"]),
        ("Intel® Core™ i5 750 or higher", ["Core i5-750"]),
        ("Intel(R) Core(TM) Ultra 7 155H", ["Core Ultra 7 155H"]),
        ("Core i7-4770S", ["Core i7-4770S"]),
    ]

    def test_cases(self):
        for text, want in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(names(matcher.match_cpus(text)), want)

    def test_unknown_model_is_estimated_between_neighbours(self):
        c = matcher.match_cpus("Intel Core i5-10505")[0]
        self.assertEqual(c["kind"], "estimate")
        self.assertTrue(9000 < c["score"] < 16000, c)

    def test_laptop_variant_of_desktop_number(self):
        desktop = matcher.match_cpus("Core i5-4460")[0]["score"]
        mobile = matcher.match_cpus("Core i5-4460U")[0]["score"]
        self.assertLess(mobile, desktop)

    def test_generic_strings(self):
        self.assertEqual(matcher.match_cpus("Intel Core i5")[0]["kind"], "generic")
        dual = matcher.match_cpus("Dual Core 2.4 GHz")[0]
        quad = matcher.match_cpus("Quad-core 3.0GHz")[0]
        self.assertLess(dual["score"], quad["score"])
        self.assertEqual(matcher.match_cpus("1.7 Ghz")[0]["kind"], "generic")

    def test_vendor_preference(self):
        cands = matcher.match_cpus("Core i7-12700 or Ryzen 7 7800X3D")
        self.assertEqual(matcher.pick_requirement(cands, "amd")["name"],
                         "Ryzen 7 7800X3D")
        self.assertEqual(matcher.pick_requirement(cands, "intel")["name"],
                         "Core i7-12700")


class GpuSelection(unittest.TestCase):
    def adapters(self, *names):
        out = []
        for n in names:
            a = specs.describe_gpu(n, 4.0)
            a["virtual"] = bool(specs._VIRTUAL.search(n))
            out.append(a)
        return out

    def test_discrete_beats_integrated_regardless_of_order(self):
        for order in (("Intel(R) Iris(R) Xe Graphics", "NVIDIA GeForce RTX 3070 Ti Laptop GPU"),
                      ("NVIDIA GeForce RTX 3070 Ti Laptop GPU", "Intel(R) Iris(R) Xe Graphics")):
            best, _ = specs.choose_gpu(self.adapters(*order))
            self.assertIn("RTX 3070 Ti", best["name"])

    def test_virtual_adapters_are_ignored(self):
        best, _ = specs.choose_gpu(self.adapters(
            "Parsec Virtual Display Adapter", "Microsoft Basic Display Adapter",
            "AMD Radeon RX 6600"))
        self.assertEqual(best["name"], "AMD Radeon RX 6600")


class Requirements(unittest.TestCase):
    STEAM = ('<strong>Minimum:</strong><br><ul class="bb_ul"><li>Requires a 64-bit '
             'processor and operating system<br></li><li><strong>OS *:</strong> '
             'Windows 7/8/10<br></li><li><strong>Processor:</strong> Intel Core '
             'i5-2500K<br></li><li><strong>Memory:</strong> 8 GB RAM<br></li><li>'
             '<strong>Graphics:</strong> GeForce GTX 780, 3 GB VRAM<br></li><li>'
             '<strong>DirectX:</strong> Version 11<br></li><li><strong>Storage:'
             '</strong> 6500 MB available space</li></ul>')

    def test_steam_block(self):
        a = requirements.analyze(requirements.parse_steam(self.STEAM))
        self.assertEqual(a["os_version"], 7.0)
        self.assertTrue(a["needs_64bit"])
        self.assertEqual(a["ram_gb"], 8)
        self.assertEqual(a["directx"], 11)
        self.assertAlmostEqual(a["storage_gb"], 6.348, places=2)
        self.assertEqual(names(a["cpu"]), ["Core i5-2500K"])
        self.assertEqual(names(a["gpu"]), ["GeForce GTX 780"])

    def test_vram_only_graphics(self):
        a = requirements.analyze({"fields": {"gpu": "256 mb video memory, shader model 3.0+"}})
        self.assertEqual(a["gpu"][0]["kind"], "generic")
        self.assertAlmostEqual(a["vram_gb"], 0.25)

    def test_pcgw_block(self):
        wiki = ("{{System requirements\n|OSfamily = Windows\n|minOS = 10\n"
                "|minCPU = Intel Core i3-3225 3.3 GHz\n|minRAM = 8 GB\n"
                "|minGPU = Intel HD Graphics 4000\n|minGPU2 = AMD Radeon Vega 8\n"
                "|recOS = 11\n|recCPU = Intel Core i5-7300U 2.6 GHz\n"
                "|recGPU = Nvidia GeForce GTX 960<ref>x</ref>\n|recVRAM = 2 GB\n}}")
        levels = requirements.parse_pcgw(wiki)
        self.assertEqual(levels["minimum"]["fields"]["os"], "Windows 10")
        a = requirements.analyze(levels["recommended"])
        self.assertEqual(names(a["gpu"]), ["GeForce GTX 960"])
        self.assertEqual(a["vram_gb"], 2)

    def test_plain_text_listing(self):
        block = ("<strong>REQUIRED</strong><br>OS: Windows Xp, Vista, 7<br>Processor: 2.0 Ghz<br>"
                 "Memory: 2.5GB<br>Hard Disk Space: 200MB<br>"
                 "Video Card: 128mb Video Memory, capable of Shader Model 2.0+<br>"
                 "DirectX®: 9.0c or Greater")
        a = requirements.analyze(requirements.parse_steam(block))
        self.assertEqual(a["os_version"], 5.1)
        self.assertEqual(a["ram_gb"], 2.5)
        self.assertEqual(a["directx"], 9)
        self.assertTrue(a["cpu"] and a["gpu"])
        self.assertTrue(requirements.has_substance(a))

    def test_empty_levels_are_dropped(self):
        for block in ("<strong>Recommended:</strong><br><ul><li>Requires a 64-bit processor "
                      "and operating system</li></ul>",
                      "<strong>Minimum:</strong><ul><li><strong>OS:</strong> TBD</li><li>"
                      "<strong>Processor:</strong> TBD</li></ul>"):
            a = requirements.analyze(requirements.parse_steam(block))
            self.assertFalse(requirements.has_substance(a), block)

    def test_series_and_wrong_family_word(self):
        self.assertIn("GTX 460", matcher.match_gpus("GTX 400 Series or Better")[0]["name"])
        self.assertEqual(names(matcher.match_gpus("GTX 2060 Super / Radeon RX 5700")),
                         ["GeForce RTX 2060 Super", "Radeon RX 5700"])

    def test_verdicts(self):
        me = {"cpu": specs.describe_cpu("Intel Core i5-8400"),
              "gpu": specs.describe_gpu("NVIDIA GeForce GTX 1060", 6.0),
              "ram_gb": 16, "directx": 12,
              "os": {"name": "Windows 10", "version": 10.0, "is_64bit": True},
              "best_drive": {"id": "C:", "free_gb": 2.0}}
        res = compare.compare(me, requirements.analyze(requirements.parse_steam(self.STEAM)))
        status = {r["key"]: r["status"] for r in res["rows"]}
        self.assertEqual(res["verdict"], "pass")
        self.assertEqual(status["storage"], "warn")
        weak = dict(me, gpu=specs.describe_gpu("Intel HD Graphics 4000"), ram_gb=4)
        res = compare.compare(weak, requirements.analyze(requirements.parse_steam(self.STEAM)))
        self.assertEqual(res["verdict"], "fail")
        # The listing asks for 3 GB of video memory; integrated graphics on a
        # 4 GB machine can borrow about 2 GB, which is not enough.
        self.assertEqual(sorted(res["failing"]), ["Graphics", "Memory", "Video memory"])


if __name__ == "__main__":
    unittest.main()
