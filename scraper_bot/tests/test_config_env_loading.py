import os
import tempfile
import unittest
from pathlib import Path

from scraper_bot.core.config import get_settings


class ConfigEnvLoadingTest(unittest.TestCase):
    def test_loads_project_env_when_running_outside_scraper_dir(self):
        repo_scraper_dir = Path(__file__).resolve().parents[1]
        env_path = repo_scraper_dir / ".env"
        original_env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else None

        old_env = dict(os.environ)
        old_cwd = Path.cwd()
        try:
            env_path.write_text(
                "\n".join(
                    [
                        "INTEL_API_BASE=http://localhost:9999",
                        "INGEST_TOKEN=test-token",
                        "SOLANA_PARALLEL_BLOCK_FETCH_WORKERS=5",
                    ]
                ),
                encoding="utf-8",
            )

            os.environ.pop("INTEL_API_BASE", None)
            os.environ.pop("INGEST_TOKEN", None)
            os.environ.pop("SOLANA_PARALLEL_BLOCK_FETCH_WORKERS", None)

            with tempfile.TemporaryDirectory() as td:
                try:
                    os.chdir(td)
                    settings = get_settings()
                finally:
                    os.chdir(old_cwd)

            self.assertEqual(settings.intel_api_base, "http://localhost:9999")
            self.assertEqual(settings.ingest_token, "test-token")
            self.assertEqual(settings.solana_parallel_block_fetch_workers, 5)
        finally:
            os.chdir(old_cwd)
            os.environ.clear()
            os.environ.update(old_env)
            if original_env_text is None:
                if env_path.exists():
                    env_path.unlink()
            else:
                env_path.write_text(original_env_text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
