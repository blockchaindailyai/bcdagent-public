import unittest

from openclaw_agent.turnkey.extension_packaging import package_openclaw_extension


class OpenClawExtensionPackagingTests(unittest.TestCase):
    def test_packaging_rejects_paths_outside_build_directory(self):
        for output in (".", "..", "openclaw_agent/dist", "openclaw_agent/dist/../../tests"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                package_openclaw_extension(output_dir=output)

    def test_packaging_builds_bundle_and_wave_completion(self):
        result = package_openclaw_extension(output_dir="openclaw_agent/dist/openclaw-extension-test")
        self.assertTrue(result.wave_1_complete)
        self.assertTrue(result.wave_2_complete)
        self.assertIn("openclaw_agent/dist/openclaw-extension-test", result.output_dir.replace("\\", "/"))
        self.assertGreaterEqual(len(result.skill_manifests), 1)


if __name__ == "__main__":
    unittest.main()
