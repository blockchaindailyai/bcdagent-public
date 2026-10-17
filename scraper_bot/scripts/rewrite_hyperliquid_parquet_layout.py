from __future__ import annotations

import argparse
import hashlib
import shutil
import time
from pathlib import Path


def _wallet_bucket(address: str, buckets: int) -> int:
    if buckets <= 1:
        return 0
    digest = hashlib.sha1(address.encode("utf-8", errors="ignore")).digest()
    value = int.from_bytes(digest[:4], byteorder="big", signed=False)
    return value % buckets


def _iter_parquet_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.parquet") if path.is_file())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite Hyperliquid wallet parquet archive to a query-optimized layout partitioned by dt and walletBucket."
        )
    )
    parser.add_argument(
        "--source-dir",
        default=".data/hyperliquid_archive/parquet_wallet_ledger",
        help="Existing parquet root (currently dt=YYYYMMDD layout).",
    )
    parser.add_argument(
        "--target-dir",
        default=".data/hyperliquid_archive/parquet_wallet_ledger_rewritten",
        help="Output parquet root (dt=.../walletBucket=... layout).",
    )
    parser.add_argument(
        "--wallet-bucket-count",
        type=int,
        default=64,
        help="Hash-bucket count for wallet partitioning.",
    )
    parser.add_argument(
        "--compression-level",
        type=int,
        default=9,
        help="ZSTD compression level for rewritten parquet files.",
    )
    parser.add_argument(
        "--max-rows-per-file",
        type=int,
        default=250_000,
        help="Target max rows per output parquet file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete target dir if it exists.",
    )
    parser.add_argument(
        "--recompute-wallet-bucket",
        action="store_true",
        help="Always recompute walletBucket from wallet column even if existing walletBucket is present.",
    )
    return parser


def _read_single_parquet_file(pq: object, file_path: Path):
    # NOTE: use ParquetFile(...).read() instead of pq.read_table(path) so pyarrow
    # doesn't invoke dataset partition schema unification for this single file path.
    return pq.ParquetFile(file_path).read()


def _dt_from_partition_path(file_path: Path, source_dir: Path) -> str:
    rel = file_path.relative_to(source_dir)
    for part in rel.parts:
        if part.startswith("dt="):
            return part.split("=", 1)[1]
    return ""


def main() -> int:
    args = _build_parser().parse_args()

    source_dir = Path(args.source_dir).resolve()
    target_dir = Path(args.target_dir).resolve()

    if not source_dir.exists() or not source_dir.is_dir():
        print(f"ERROR: Source dir not found: {source_dir}")
        return 2

    if target_dir.exists():
        if not args.force:
            print(f"ERROR: Target dir already exists: {target_dir}. Use --force to overwrite.")
            return 2
        shutil.rmtree(target_dir)

    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.compute as pc  # type: ignore
        import pyarrow.dataset as ds  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ModuleNotFoundError as exc:
        print("ERROR: PyArrow is required. Install with: pip install pyarrow")
        raise SystemExit(2) from exc

    input_files = _iter_parquet_files(source_dir)
    if not input_files:
        print(f"No parquet files found under {source_dir}")
        return 0

    parquet_format = ds.ParquetFileFormat()
    file_options = parquet_format.make_write_options(
        compression="zstd",
        compression_level=int(args.compression_level),
        use_dictionary=["wallet", "market", "side", "dt", "walletBucket"],
    )

    max_rows_per_file = max(10_000, int(args.max_rows_per_file))
    # PyArrow requires row-group cap <= file cap.
    max_rows_per_group = min(128_000, max_rows_per_file)

    total_files = len(input_files)
    total_rows = 0
    started = time.time()

    for idx, file_path in enumerate(input_files, start=1):
        table = _read_single_parquet_file(pq, file_path)

        names = set(table.column_names)
        if "wallet" not in names:
            print(f"[skip] {file_path} (missing wallet column)")
            continue

        # Normalize dt to string to avoid incompatible-type failures between sources.
        if "dt" not in names:
            dt_val = _dt_from_partition_path(file_path, source_dir)
            if not dt_val:
                print(f"[skip] {file_path} (missing dt column/partition)")
                continue
            dt_arr = pa.array([dt_val] * table.num_rows, type=pa.string())
            table = table.append_column("dt", dt_arr)
        else:
            dt_idx = table.column_names.index("dt")
            dt_str = pc.cast(table.column(dt_idx), pa.string(), safe=False)
            table = table.set_column(dt_idx, "dt", dt_str)

        needs_bucket = args.recompute_wallet_bucket or "walletBucket" not in names
        if needs_bucket:
            wallets = table.column("wallet").to_pylist()
            wallet_buckets = [
                _wallet_bucket(str(wallet or "").strip().lower(), int(args.wallet_bucket_count))
                for wallet in wallets
            ]
            bucket_arr = pa.array(wallet_buckets, type=pa.int16())
            if "walletBucket" in set(table.column_names):
                bucket_idx = table.column_names.index("walletBucket")
                table = table.set_column(bucket_idx, "walletBucket", bucket_arr)
            else:
                table = table.append_column("walletBucket", bucket_arr)
        else:
            # Ensure consistent physical type for partition column.
            bucket_idx = table.column_names.index("walletBucket")
            bucket_arr = pc.cast(table.column(bucket_idx), pa.int16(), safe=False)
            table = table.set_column(bucket_idx, "walletBucket", bucket_arr)

        ds.write_dataset(
            data=table,
            base_dir=target_dir,
            format=parquet_format,
            file_options=file_options,
            partitioning=ds.partitioning(
                pa.schema([pa.field("dt", pa.string()), pa.field("walletBucket", pa.int16())]),
                flavor="hive",
            ),
            existing_data_behavior="overwrite_or_ignore",
            basename_template=f"part-{idx:07d}-{{i}}.parquet",
            max_rows_per_file=max_rows_per_file,
            max_rows_per_group=max_rows_per_group,
        )

        total_rows += table.num_rows
        if idx % 25 == 0 or idx == total_files:
            elapsed = max(0.001, time.time() - started)
            files_per_sec = idx / elapsed
            rows_per_sec = total_rows / elapsed
            print(
                f"[{idx}/{total_files}] files | rows={total_rows:,} | "
                f"files/s={files_per_sec:0.2f} | rows/s={rows_per_sec:,.0f}"
            )

    elapsed = max(0.001, time.time() - started)
    print("Rewrite complete")
    print(f"Source: {source_dir}")
    print(f"Target: {target_dir}")
    print(f"Files processed: {total_files}")
    print(f"Rows written: {total_rows:,}")
    print(f"Elapsed seconds: {elapsed:0.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
