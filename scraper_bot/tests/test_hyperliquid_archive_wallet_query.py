import importlib.util
import sys
import tempfile
import unittest

import pytest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "hyperliquid_archive_wallet_query.py"
spec = importlib.util.spec_from_file_location("hyperliquid_archive_wallet_query", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class HyperliquidArchiveWalletQueryTest(unittest.TestCase):
    def test_normalize_wallet(self):
        self.assertEqual(module._normalize_wallet(" 0xAbC "), "0xabc")

    def test_query_wallet_daily_pnl(self):
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day_dir = root / "dt=20250727"
            day_dir.mkdir(parents=True, exist_ok=True)
            file_path = day_dir / "fills.parquet"

            table = pa.Table.from_pylist(
                [
                    {
                        "dt": "20250727",
                        "wallet": "0xabc",
                        "ts": 100,
                        "notionalUsd": 10.0,
                        "realizedPnlUsd": 4.0,
                        "feesUsd": 1.0,
                    },
                    {
                        "dt": "20250727",
                        "wallet": "0xabc",
                        "ts": 200,
                        "notionalUsd": 20.0,
                        "realizedPnlUsd": -1.0,
                        "feesUsd": 2.0,
                    },
                    {
                        "dt": "20250727",
                        "wallet": "0xdef",
                        "ts": 300,
                        "notionalUsd": 100.0,
                        "realizedPnlUsd": 20.0,
                        "feesUsd": 3.0,
                    },
                ]
            )
            pq.write_table(table, file_path)

            rows = module.query_wallet_daily_pnl(parquet_root=root, wallet="0xAbC", min_day="20250727")

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["dt"], "20250727")
            self.assertEqual(row["ts"], 200)
            self.assertAlmostEqual(row["notionalUsd"], 30.0)
            self.assertAlmostEqual(row["realizedPnlUsd"], 3.0)
            self.assertAlmostEqual(row["feesUsd"], 3.0)
            self.assertAlmostEqual(row["netPnlUsd"], 0.0)
            self.assertEqual(row["trades"], 2)


    def test_query_wallet_daily_pnl_rewritten_layout_uses_wallet_bucket_partition(self):
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wallet = "0xabc"
            wallet_bucket = module._wallet_bucket(wallet, 64)
            day_dir = root / "dt=20250727" / f"walletBucket={wallet_bucket}"
            day_dir.mkdir(parents=True, exist_ok=True)
            wrong_bucket = (wallet_bucket + 1) % 64
            wrong_bucket_dir = root / "dt=20250727" / f"walletBucket={wrong_bucket}"
            wrong_bucket_dir.mkdir(parents=True, exist_ok=True)

            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "dt": "20250727",
                            "wallet": wallet,
                            "walletBucket": wallet_bucket,
                            "ts": 100,
                            "notionalUsd": 10.0,
                            "realizedPnlUsd": 2.0,
                            "feesUsd": 1.0,
                        }
                    ]
                ),
                day_dir / "fills.parquet",
            )
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "dt": "20250727",
                            "wallet": wallet,
                            "walletBucket": wrong_bucket,
                            "ts": 101,
                            "notionalUsd": 999.0,
                            "realizedPnlUsd": 999.0,
                            "feesUsd": 0.0,
                        }
                    ]
                ),
                wrong_bucket_dir / "fills.parquet",
            )

            rows = module.query_wallet_daily_pnl(parquet_root=root, wallet=wallet, min_day="20250727")

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertAlmostEqual(row["notionalUsd"], 10.0)
            self.assertAlmostEqual(row["realizedPnlUsd"], 2.0)
            self.assertAlmostEqual(row["feesUsd"], 1.0)


    def test_query_wallet_daily_pnl_rewritten_layout_handles_mixed_wallet_bucket_types(self):
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wallet = "0xabc"
            wallet_bucket = module._wallet_bucket(wallet, 64)
            good_dir = root / "dt=20250727" / f"walletBucket={wallet_bucket}"
            good_dir.mkdir(parents=True, exist_ok=True)

            bad_bucket = (wallet_bucket + 1) % 64
            bad_dir = root / "dt=20250727" / f"walletBucket={bad_bucket}"
            bad_dir.mkdir(parents=True, exist_ok=True)

            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "dt": "20250727",
                            "wallet": wallet,
                            "walletBucket": wallet_bucket,
                            "ts": 123,
                            "notionalUsd": 5.0,
                            "realizedPnlUsd": 1.0,
                            "feesUsd": 0.5,
                        }
                    ]
                ),
                good_dir / "fills.parquet",
            )

            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "dt": "20250727",
                            "wallet": "0xdef",
                            "walletBucket": bad_bucket,
                            "ts": 999,
                            "notionalUsd": 100.0,
                            "realizedPnlUsd": 20.0,
                            "feesUsd": 1.0,
                        }
                    ],
                    schema=pa.schema(
                        [
                            pa.field("dt", pa.string()),
                            pa.field("wallet", pa.string()),
                            pa.field("walletBucket", pa.int64()),
                            pa.field("ts", pa.int64()),
                            pa.field("notionalUsd", pa.float64()),
                            pa.field("realizedPnlUsd", pa.float64()),
                            pa.field("feesUsd", pa.float64()),
                        ]
                    ),
                ),
                bad_dir / "fills.parquet",
            )

            rows = module.query_wallet_daily_pnl(parquet_root=root, wallet=wallet, min_day="20250727")

            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]["notionalUsd"], 5.0)

    def test_query_wallet_daily_pnl_rewritten_layout_handles_mixed_types_same_bucket_across_days(self):
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wallet = "0xabc"
            wallet_bucket = module._wallet_bucket(wallet, 64)

            day1_dir = root / "dt=20250727" / f"walletBucket={wallet_bucket}"
            day2_dir = root / "dt=20250728" / f"walletBucket={wallet_bucket}"
            day1_dir.mkdir(parents=True, exist_ok=True)
            day2_dir.mkdir(parents=True, exist_ok=True)

            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "dt": "20250727",
                            "wallet": wallet,
                            "walletBucket": wallet_bucket,
                            "ts": 100,
                            "notionalUsd": 2.0,
                            "realizedPnlUsd": 1.0,
                            "feesUsd": 0.1,
                        }
                    ],
                    schema=pa.schema(
                        [
                            pa.field("dt", pa.string()),
                            pa.field("wallet", pa.string()),
                            pa.field("walletBucket", pa.int16()),
                            pa.field("ts", pa.int64()),
                            pa.field("notionalUsd", pa.float64()),
                            pa.field("realizedPnlUsd", pa.float64()),
                            pa.field("feesUsd", pa.float64()),
                        ]
                    ),
                ),
                day1_dir / "fills.parquet",
            )

            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "dt": "20250728",
                            "wallet": wallet,
                            "walletBucket": wallet_bucket,
                            "ts": 200,
                            "notionalUsd": 3.0,
                            "realizedPnlUsd": 0.5,
                            "feesUsd": 0.2,
                        }
                    ],
                    schema=pa.schema(
                        [
                            pa.field("dt", pa.string()),
                            pa.field("wallet", pa.string()),
                            pa.field("walletBucket", pa.int64()),
                            pa.field("ts", pa.int64()),
                            pa.field("notionalUsd", pa.float64()),
                            pa.field("realizedPnlUsd", pa.float64()),
                            pa.field("feesUsd", pa.float64()),
                        ]
                    ),
                ),
                day2_dir / "fills.parquet",
            )

            rows = module.query_wallet_daily_pnl(parquet_root=root, wallet=wallet, min_day="20250727")

            self.assertEqual(len(rows), 2)
            self.assertAlmostEqual(sum(row["notionalUsd"] for row in rows), 5.0)



    def test_resolve_bucket_partition_dirs_falls_back_to_detected_bucket_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wallet = "0xabc"
            expected_bucket_count = 128
            bucket = module._wallet_bucket(wallet, expected_bucket_count)
            (root / "dt=20250727" / f"walletBucket={bucket}").mkdir(parents=True, exist_ok=True)

            partition_dirs, resolved, has_rewritten = module._resolve_bucket_partition_dirs(
                parquet_root=root,
                min_day="20250727",
                wallet=wallet,
                preferred_bucket_count=64,
            )

            self.assertEqual(resolved, expected_bucket_count)
            self.assertTrue(has_rewritten)
            self.assertEqual(len(partition_dirs), 1)
            self.assertTrue(str(partition_dirs[0]).endswith(f"walletBucket={bucket}"))



    def test_query_wallet_daily_pnl_rewritten_layout_no_bucket_match_returns_empty_without_legacy_scan(self):
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day_dir = root / "dt=20250727" / "walletBucket=0"
            day_dir.mkdir(parents=True, exist_ok=True)

            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "dt": "20250727",
                            "wallet": "0xdef",
                            "ts": 10,
                            "notionalUsd": 1.0,
                            "realizedPnlUsd": 0.5,
                            "feesUsd": 0.1,
                        }
                    ]
                ),
                day_dir / "fills.parquet",
            )

            rows = module.query_wallet_daily_pnl(parquet_root=root, wallet="0xabc", min_day="20250727")
            self.assertEqual(rows, [])

    def test_query_wallet_trades_with_missing_optional_columns(self):
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day_dir = root / "dt=20250727"
            day_dir.mkdir(parents=True, exist_ok=True)
            file_path = day_dir / "fills.parquet"

            table = pa.Table.from_pylist(
                [
                    {
                        "dt": "20250727",
                        "wallet": "0xabc",
                        "ts": 101,
                        "px": 123.45,
                        "sz": 2.0,
                    }
                ]
            )
            pq.write_table(table, file_path)

            rows = module.query_wallet_trades(
                parquet_root=root,
                wallet="0xAbC",
                min_day="20250727",
                max_trades=100,
            )

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["dt"], "20250727")
            self.assertEqual(row["ts"], 101)
            self.assertEqual(row["market"], "")
            self.assertEqual(row["side"], "")
            self.assertAlmostEqual(row["price"], 123.45)
            self.assertAlmostEqual(row["size"], 2.0)
            self.assertEqual(row["tradeId"], "")
            self.assertEqual(row["txHash"], "")

    def test_query_wallet_trades_returns_empty_when_missing_critical_columns(self):
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day_dir = root / "dt=20250727"
            day_dir.mkdir(parents=True, exist_ok=True)
            file_path = day_dir / "fills.parquet"

            table = pa.Table.from_pylist(
                [
                    {
                        "dt": "20250727",
                        "wallet": "0xabc",
                        "px": 123.45,
                    }
                ]
            )
            pq.write_table(table, file_path)

            rows = module.query_wallet_trades(
                parquet_root=root,
                wallet="0xAbC",
                min_day="20250727",
                max_trades=100,
            )

            self.assertEqual(rows, [])

    def test_query_wallet_daily_pnl_with_missing_optional_columns(self):
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day_dir = root / "dt=20250727"
            day_dir.mkdir(parents=True, exist_ok=True)
            file_path = day_dir / "fills.parquet"

            table = pa.Table.from_pylist(
                [
                    {
                        "wallet": "0xabc",
                        "ts": 100,
                    }
                ]
            )
            pq.write_table(table, file_path)

            rows = module.query_wallet_daily_pnl(
                parquet_root=root,
                wallet="0xAbC",
                min_day="20250727",
            )

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["dt"], "")
            self.assertEqual(row["ts"], 100)
            self.assertAlmostEqual(row["notionalUsd"], 0.0)
            self.assertAlmostEqual(row["realizedPnlUsd"], 0.0)
            self.assertAlmostEqual(row["feesUsd"], 0.0)
            self.assertAlmostEqual(row["netPnlUsd"], 0.0)
            self.assertEqual(row["trades"], 1)

    def test_query_wallet_daily_pnl_returns_empty_when_missing_critical_columns(self):
        pa = pytest.importorskip("pyarrow")
        pq = pytest.importorskip("pyarrow.parquet")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day_dir = root / "dt=20250727"
            day_dir.mkdir(parents=True, exist_ok=True)
            file_path = day_dir / "fills.parquet"

            table = pa.Table.from_pylist(
                [
                    {
                        "wallet": "0xabc",
                        "notionalUsd": 25.0,
                    }
                ]
            )
            pq.write_table(table, file_path)

            rows = module.query_wallet_daily_pnl(
                parquet_root=root,
                wallet="0xAbC",
                min_day="20250727",
            )

            self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
