"""Runs tests/js/logic.test.js under Node when Node is installed (CI has it)."""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class WebLogic(unittest.TestCase):
    def test_logic_js(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not installed; the browser logic tests need it.")
        proc = subprocess.run([node, os.path.join(HERE, "js", "logic.test.js")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
