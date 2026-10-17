from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import re
from pathlib import Path
from typing import Any


def _normalize_wallet(value: str) -> str:
    return str(value or "").strip().lower()


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _wallet_bucket(address: str, bucket_count: int) -> int:
    if bucket_count <= 1:
        return 0
    digest = hashlib.sha1(address.encode("utf-8", errors="ignore")).digest()
    value = int.from_bytes(digest[:4], byteorder="big", signed=False)
    return value % bucket_count


_DT_DIR_RE = re.compile(r"^dt=(\d{8})$")
_BUCKET_DIR_RE = re.compile(r"^walletBucket=(\d+)$")


def _collect_rewritten_dt_dirs(parquet_root: Path, min_day: str) -> list[Path]:
    if not parquet_root.exists() or not parquet_root.is_dir():
        return []

    dt_dirs: list[Path] = []
    for child in parquet_root.iterdir():
        if not child.is_dir():
            continue
        match = _DT_DIR_RE.match(child.name)
        if not match:
            continue
        day = match.group(1)
        if day < min_day:
            continue

        dt_dirs.append(child)

    dt_dirs.sort(key=lambda p: p.name)
    return dt_dirs


def _resolve_bucket_partition_dirs(
    *, parquet_root: Path, min_day: str, wallet: str, preferred_bucket_count: int
) -> tuple[list[Path], int, bool]:
    wallet_norm = _normalize_wallet(wallet)
    dt_dirs = _collect_rewritten_dt_dirs(parquet_root, min_day)
    has_rewritten_partitions = any(
        child.is_dir() and _BUCKET_DIR_RE.match(child.name)
        for dt_dir in dt_dirs for child in dt_dir.iterdir()
    )

    candidate_counts: list[int] = []

    def _add_count(value: int) -> None:
        if value <= 0:
            return
        if value not in candidate_counts:
            candidate_counts.append(value)

    _add_count(max(1, preferred_bucket_count))

    for guess in (64, 128, 256, 512, 1024, 2048, 4096, 8192, 32, 16, 8, 4, 2, 1):
        _add_count(guess)

    for bucket_count in candidate_counts:
        wallet_bucket = _wallet_bucket(wallet_norm, bucket_count)
        partition_dirs = []
        partition_name = f"walletBucket={wallet_bucket}"
        for dt_dir in dt_dirs:
            partition_dir = dt_dir / partition_name
            if partition_dir.is_dir():
                partition_dirs.append(partition_dir)
        if partition_dirs:
            return partition_dirs, bucket_count, has_rewritten_partitions

    return [], max(1, preferred_bucket_count), has_rewritten_partitions


def _iter_partition_parquet_files(partition_dirs: list[Path]):
    files: list[Path] = []
    for partition_dir in partition_dirs:
        files.extend(path for path in partition_dir.glob("*.parquet") if path.is_file())
    for file_path in sorted(files):
        yield file_path


def _iter_wallet_record_batches(*, parquet_root: Path, wallet: str, min_day: str, wallet_bucket_count: int):
    wallet_norm = _normalize_wallet(wallet)
    if not wallet_norm:
        return

    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.compute as pc  # type: ignore
        import pyarrow.dataset as ds  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("PyArrow is required for archive wallet query") from exc

    partition_dirs, resolved_bucket_count, has_rewritten_partitions = _resolve_bucket_partition_dirs(
        parquet_root=parquet_root,
        min_day=min_day,
        wallet=wallet_norm,
        preferred_bucket_count=wallet_bucket_count,
    )
    wallet_bucket = _wallet_bucket(wallet_norm, resolved_bucket_count)

    requested_columns = [
        "dt",
        "ts",
        "market",
        "side",
        "px",
        "sz",
        "notionalUsd",
        "realizedPnlUsd",
        "feesUsd",
        "tid",
        "txHash",
    ]
    partitioning = ds.partitioning(pa.schema([pa.field("dt", pa.string())]), flavor="hive")

    query_schema = pa.schema(
        [
            pa.field("wallet", pa.string()),
            pa.field("ts", pa.int64()),
            pa.field("dt", pa.string()),
            pa.field("market", pa.string()),
            pa.field("side", pa.string()),
            pa.field("px", pa.float64()),
            pa.field("sz", pa.float64()),
            pa.field("notionalUsd", pa.float64()),
            pa.field("realizedPnlUsd", pa.float64()),
            pa.field("feesUsd", pa.float64()),
            pa.field("tid", pa.string()),
            pa.field("txHash", pa.string()),
            pa.field("walletBucket", pa.int32()),
        ]
    )

    dataset_sources: list[Path | list[Path] | str]
    if partition_dirs:
        dataset_sources = [partition_dirs]
    elif has_rewritten_partitions:
        # Rewritten archive exists, but this wallet has no matching bucket partitions.
        return
    else:
        dataset_sources = [parquet_root]

    for source in dataset_sources:
        try:
            if has_rewritten_partitions:
                dataset = ds.dataset(source, format="parquet", partitioning=partitioning, schema=query_schema)
            else:
                # Preserve the physical legacy schema: inventing nullable wallet/ts
                # columns would defeat the required-column checks below.
                dataset = ds.dataset(source, format="parquet")
        except Exception:
            if not partition_dirs:
                continue
            for file_source in _iter_partition_parquet_files(partition_dirs):
                try:
                    dataset = ds.dataset(file_source, format="parquet", partitioning=partitioning, schema=query_schema)
                except Exception:
                    continue

                available_columns = set(dataset.schema.names)
                if "wallet" not in available_columns or "ts" not in available_columns:
                    continue

                selected_columns = [column for column in requested_columns if column in available_columns]
                filter_expr = pc.field("wallet") == wallet_norm
                if "dt" in available_columns:
                    filter_expr = filter_expr & (pc.field("dt") >= min_day)

                scanner = dataset.scanner(
                    columns=selected_columns,
                    filter=filter_expr,
                    batch_size=65_536,
                    use_threads=True,
                )
                yield from scanner.to_batches()
            continue
        available_columns = set(dataset.schema.names)
        if "wallet" not in available_columns or "ts" not in available_columns:
            continue

        selected_columns = [column for column in requested_columns if column in available_columns]

        filter_expr = pc.field("wallet") == wallet_norm
        if "dt" in available_columns:
            filter_expr = filter_expr & (pc.field("dt") >= min_day)
        # The supplied schema includes walletBucket even for legacy files, where
        # values are null. Applying a bucket filter there would discard all rows.
        if has_rewritten_partitions and "walletBucket" in available_columns and not partition_dirs:
            filter_expr = filter_expr & (pc.field("walletBucket") == wallet_bucket)

        scanner = dataset.scanner(
            columns=selected_columns,
            filter=filter_expr,
            batch_size=65_536,
            use_threads=True,
        )

        yield from scanner.to_batches()


def _batch_column_or_none(batch: Any, name: str):
    idx = batch.schema.get_field_index(name)
    if idx < 0:
        return None
    return batch.column(idx)


def _batch_as_non_null(batch: Any, name: str, cast_type: Any, fill_value: Any):
    import pyarrow as pa  # type: ignore
    import pyarrow.compute as pc  # type: ignore

    column = _batch_column_or_none(batch, name)
    if column is None:
        return pa.array([fill_value] * batch.num_rows, type=cast_type)
    casted = pc.cast(column, cast_type, safe=False)
    return pc.fill_null(casted, fill_value)


def _accumulate_daily_pnl_batch(*, batch: Any, min_day: str, by_day: dict[str, dict[str, Any]]) -> None:
    import pyarrow as pa  # type: ignore

    if batch.num_rows <= 0:
        return

    dt_arr = _batch_as_non_null(batch, "dt", pa.string(), "")
    ts_arr = _batch_as_non_null(batch, "ts", pa.int64(), 0)
    notional_arr = _batch_as_non_null(batch, "notionalUsd", pa.float64(), 0.0)
    realized_arr = _batch_as_non_null(batch, "realizedPnlUsd", pa.float64(), 0.0)
    fees_arr = _batch_as_non_null(batch, "feesUsd", pa.float64(), 0.0)

    grouped = (
        pa.table(
            {
                "dt": dt_arr,
                "ts": ts_arr,
                "notionalUsd": notional_arr,
                "realizedPnlUsd": realized_arr,
                "feesUsd": fees_arr,
            }
        )
        .group_by("dt")
        .aggregate(
            [
                ("ts", "max"),
                ("notionalUsd", "sum"),
                ("realizedPnlUsd", "sum"),
                ("feesUsd", "sum"),
                ("dt", "count"),
            ]
        )
    )
    groups = grouped.to_pylist()

    for row in groups:
        dt = _coerce_str(row.get("dt", "")).strip()
        if dt and (not dt.isdigit() or dt < min_day):
            continue

        ts = _coerce_int(row.get("ts_max", 0))
        notional = _coerce_float(row.get("notionalUsd_sum", 0.0))
        realized = _coerce_float(row.get("realizedPnlUsd_sum", 0.0))
        fees = _coerce_float(row.get("feesUsd_sum", 0.0))
        trades = _coerce_int(row.get("dt_count", 0))

        bucket = by_day.setdefault(
            dt,
            {
                "dt": dt,
                "ts": ts,
                "notionalUsd": 0.0,
                "realizedPnlUsd": 0.0,
                "feesUsd": 0.0,
                "netPnlUsd": 0.0,
                "trades": 0,
            },
        )
        bucket["ts"] = max(_coerce_int(bucket.get("ts")), ts)
        bucket["notionalUsd"] += notional
        bucket["realizedPnlUsd"] += realized
        bucket["feesUsd"] += fees
        bucket["netPnlUsd"] += realized - fees
        bucket["trades"] += trades


def _accumulate_trade_heap_batch(*, batch: Any, min_day: str, max_trades: int, trade_heap: list[tuple[int, int, dict[str, Any]]], seq: int) -> int:
    import pyarrow as pa  # type: ignore
    import pyarrow.compute as pc  # type: ignore

    if max_trades <= 0 or batch.num_rows <= 0:
        return seq

    dt_arr = _batch_as_non_null(batch, "dt", pa.string(), "")
    if min_day:
        dt_mask = pc.or_(pc.equal(dt_arr, ""), pc.greater_equal(dt_arr, min_day))
    else:
        dt_mask = None

    ts_arr = _batch_as_non_null(batch, "ts", pa.int64(), 0)
    market_arr = _batch_as_non_null(batch, "market", pa.string(), "")
    side_arr = _batch_as_non_null(batch, "side", pa.string(), "")
    px_arr = _batch_as_non_null(batch, "px", pa.float64(), 0.0)
    sz_arr = _batch_as_non_null(batch, "sz", pa.float64(), 0.0)
    notional_arr = _batch_as_non_null(batch, "notionalUsd", pa.float64(), 0.0)
    tid_arr = _batch_as_non_null(batch, "tid", pa.string(), "")
    tx_hash_arr = _batch_as_non_null(batch, "txHash", pa.string(), "")

    trade_table = pa.table(
        {
            "dt": dt_arr,
            "ts": ts_arr,
            "market": market_arr,
            "side": side_arr,
            "px": px_arr,
            "sz": sz_arr,
            "notionalUsd": notional_arr,
            "tid": tid_arr,
            "txHash": tx_hash_arr,
        }
    )

    if dt_mask is not None:
        trade_table = trade_table.filter(dt_mask)
    if trade_table.num_rows <= 0:
        return seq

    batch_top = trade_table.sort_by([("ts", "descending")]).slice(0, max_trades).to_pylist()
    for row in batch_top:
        ts = _coerce_int(row.get("ts", 0))
        trade_row = {
            "dt": _coerce_str(row.get("dt", "")),
            "ts": ts,
            "market": _coerce_str(row.get("market", "")),
            "side": _coerce_str(row.get("side", "")),
            "price": _coerce_float(row.get("px", 0.0)),
            "size": _coerce_float(row.get("sz", 0.0)),
            "notionalUsd": _coerce_float(row.get("notionalUsd", 0.0)),
            "tradeId": _coerce_str(row.get("tid", "")),
            "txHash": _coerce_str(row.get("txHash", "")),
        }

        item = (ts, seq, trade_row)
        seq += 1
        if len(trade_heap) < max_trades:
            heapq.heappush(trade_heap, item)
        elif item[0] > trade_heap[0][0]:
            heapq.heapreplace(trade_heap, item)

    return seq


def query_wallet_daily_pnl(*, parquet_root: Path, wallet: str, min_day: str) -> list[dict[str, Any]]:
    rows, _ = _query_wallet(
        parquet_root=parquet_root,
        wallet=wallet,
        min_day=min_day,
        max_trades=0,
        wallet_bucket_count=64,
    )
    return rows


def query_wallet_trades(
    parquet_root: Path,
    wallet: str,
    min_day: str,
    max_trades: int,
) -> list[dict[str, Any]]:
    _, trade_rows = _query_wallet(
        parquet_root=parquet_root,
        wallet=wallet,
        min_day=min_day,
        max_trades=max_trades,
        wallet_bucket_count=64,
    )
    return trade_rows


def _query_wallet(
    *, parquet_root: Path, wallet: str, min_day: str, max_trades: int, wallet_bucket_count: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_day: dict[str, dict[str, Any]] = {}
    trade_heap: list[tuple[int, int, dict[str, Any]]] = []
    seq = 0
    max_trades = max(0, max_trades)

    for batch in (
        _iter_wallet_record_batches(
            parquet_root=parquet_root,
            wallet=wallet,
            min_day=min_day,
            wallet_bucket_count=wallet_bucket_count,
        )
        or []
    ):
        _accumulate_daily_pnl_batch(batch=batch, min_day=min_day, by_day=by_day)
        seq = _accumulate_trade_heap_batch(
            batch=batch,
            min_day=min_day,
            max_trades=max_trades,
            trade_heap=trade_heap,
            seq=seq,
        )

    rows = sorted(by_day.values(), key=lambda item: (str(item["dt"]), _coerce_int(item["ts"])))

    trade_rows: list[dict[str, Any]] = []
    if max_trades > 0 and trade_heap:
        trade_rows = [entry[2] for entry in sorted(trade_heap, key=lambda item: (item[0], item[1]))]

    return rows, trade_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Query Hyperliquid archive parquet by wallet")
    parser.add_argument("--parquet-dir", required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--start-date", default="20250727", help="Minimum partition day YYYYMMDD")
    parser.add_argument("--max-trades", type=int, default=0, help="Max trade rows to return per wallet")
    parser.add_argument("--wallet-bucket-count", type=int, default=64, help="Bucket count used in rewritten archive")
    args = parser.parse_args()

    parquet_root = Path(args.parquet_dir)
    if not parquet_root.exists() or not parquet_root.is_dir():
        print(json.dumps({"error": f"Parquet directory not found: {parquet_root}"}))
        return 2

    rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    try:
        rows, trade_rows = _query_wallet(
            parquet_root=parquet_root,
            wallet=args.wallet,
            min_day=str(args.start_date or "20250727"),
            max_trades=_coerce_int(args.max_trades),
            wallet_bucket_count=max(1, _coerce_int(args.wallet_bucket_count, 64)),
        )
    except Exception as exc:
        errors.append({"stage": "query", "type": exc.__class__.__name__, "message": str(exc)})

    payload: dict[str, Any] = {
        "wallet": _normalize_wallet(args.wallet),
        "rows": rows,
        "tradeRows": trade_rows,
    }
    if errors:
        payload["errors"] = errors

    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
