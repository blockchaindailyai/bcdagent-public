from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.request

from .config import load_turnkey_config
from .jtrx_engine import JTRXDecisionEngine, JTRXDecisionIntent, JTRXExecutor, normalize_jtrx_action
from .onchain import TronBroadcastError, TronTxBroadcaster, _encode_types
from .runtime import build_runtime_status, check_jtrx_borrow_readiness, monitor_account_health
from .indexing import build_indexer, emit_execution_event_safe
from .signing import SIGNER_ROLE_EXECUTION, resolve_signing_context
from .approvals import build_approval_artifacts, build_audit_bundle, parse_approval_identities
from .mcp_setup import build_nile_mcp_server_setup_plan, setup_plan_to_dict
from .search_service import search_reviewer_operator_events
from .extension_packaging import package_openclaw_extension, packaging_result_to_dict
from .autonomous import decide_action_from_metrics, parse_action_sequence, resolve_effective_thresholds, run_autonomous_cycles
from .sunswap_executor import (
    SmartRouterStrategySwapRequest,
    SmartRouterSwapError,
    SmartRouterSwapRequest,
    request_to_dict,
    run_smart_router_strategy_swap,
    run_smart_router_swap,
    strategy_request_to_dict,
    ensure_sufficient_trc20_balance,
)


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _run_health(args: argparse.Namespace) -> int:
    status = build_runtime_status(args.config)
    print(json.dumps({
        "chain_healthy": status.chain_healthy,
        "justlend_healthy": status.justlend_healthy,
        "details": status.details,
    }, indent=2))
    return 0 if status.chain_healthy and status.justlend_healthy else 1




def _run_monitor_account(args: argparse.Namespace) -> int:
    payload = monitor_account_health(args.owner_address, args.config)
    print(json.dumps(payload, indent=2))
    return 0


def _run_check_borrow_ready(args: argparse.Namespace) -> int:
    payload = check_jtrx_borrow_readiness(args.config)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ready") else 2



def _run_setup_mcp_testnet(args: argparse.Namespace) -> int:
    cfg = load_turnkey_config(args.config)
    plan = build_nile_mcp_server_setup_plan(
        cfg,
        mcp_server_tron_url=args.mcp_server_tron_url,
        subgraph_indexer_url=args.subgraph_indexer_url,
    )
    print(json.dumps(setup_plan_to_dict(plan), indent=2))
    return 0


def _run_search_reviewer_operator(args: argparse.Namespace) -> int:
    cfg = load_turnkey_config(args.config)
    payload = search_reviewer_operator_events(
        cfg,
        query=args.query,
        role_filter=args.role,
        limit=args.limit,
    )
    print(json.dumps(payload, indent=2))
    return 0


def _run_package_openclaw_extension(args: argparse.Namespace) -> int:
    result = package_openclaw_extension(output_dir=args.output_dir)
    print(json.dumps(packaging_result_to_dict(result), indent=2))
    return 0 if result.wave_1_complete and result.wave_2_complete else 4


def _run_smart_router_swap(args: argparse.Namespace) -> int:
    now_epoch_s = int(time.time())
    if args.deadline <= now_epoch_s:
        raise ValueError(
            f"swap_deadline_expired: deadline={args.deadline} now={now_epoch_s} "
            "(use a future unix timestamp, usually now + 600~1200 seconds on Nile)"
        )
    if args.receipt_timeout_s <= 0:
        raise ValueError("receipt_timeout_s_must_be_positive")

    req = SmartRouterSwapRequest(
        full_host=args.full_host,
        smart_router=args.smart_router,
        token_in=args.token_in,
        token_out=args.token_out,
        amount_in=args.amount_in,
        amount_out_min=args.amount_out_min,
        to=args.to_address,
        deadline=args.deadline,
        pool_version=[x.strip() for x in args.pool_version.split(",") if x.strip()],
        version_len=[int(x.strip()) for x in args.version_len.split(",") if x.strip()],
        fees=[int(x.strip()) for x in args.fees.split(",") if x.strip()],
        fee_limit=args.fee_limit,
        auto_approve=args.auto_approve,
        wait_for_receipt=args.wait_for_receipt,
        receipt_timeout_s=args.receipt_timeout_s,
        auto_amount_out_min=args.auto_amount_out_min,
        quote_slippage_bps=args.quote_slippage_bps,
    )
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "request": request_to_dict(req)}, indent=2))
        return 0

    cfg = load_turnkey_config(args.config)
    preflight = ensure_sufficient_trc20_balance(
        req.full_host,
        token=req.token_in,
        owner=cfg.addresses.treasury,
        required_amount=req.amount_in,
    )

    result = run_smart_router_swap(req)
    print(json.dumps({"status": "ok", "request": request_to_dict(req), "preflight": preflight, "result": result}, indent=2))
    return 0


def _run_smart_router_strategy_swap(args: argparse.Namespace) -> int:
    now_epoch_s = int(time.time())
    if args.deadline <= now_epoch_s:
        raise ValueError(
            f"swap_deadline_expired: deadline={args.deadline} now={now_epoch_s} "
            "(use a future unix timestamp, usually now + 600~1200 seconds on Nile)"
        )
    if args.receipt_timeout_s <= 0:
        raise ValueError("receipt_timeout_s_must_be_positive")

    cfg = load_turnkey_config(args.config)
    owner = args.owner_address or getattr(cfg.addresses, "operator", "")
    permission_id = args.permission_id if args.permission_id is not None else getattr(cfg, "permission_id", 0)
    signing = resolve_signing_context(signer_role=args.signer_role, explicit_private_key=args.private_key)
    private_key = signing.private_key_hex
    strategy_module = args.strategy_module or cfg.addresses.strategy_module

    req = SmartRouterStrategySwapRequest(
        config=cfg,
        full_host=args.full_host,
        strategy_module=strategy_module,
        smart_router=args.smart_router,
        token_in=args.token_in,
        token_out=args.token_out,
        amount_in=args.amount_in,
        amount_out_min=args.amount_out_min,
        to=args.to_address,
        deadline=args.deadline,
        pool_version=[x.strip() for x in args.pool_version.split(",") if x.strip()],
        version_len=[int(x.strip()) for x in args.version_len.split(",") if x.strip()],
        fees=[int(x.strip()) for x in args.fees.split(",") if x.strip()],
        fee_limit=args.fee_limit,
        wait_for_receipt=args.wait_for_receipt,
        receipt_timeout_s=args.receipt_timeout_s,
        auto_amount_out_min=args.auto_amount_out_min,
        quote_slippage_bps=args.quote_slippage_bps,
        owner_address=owner,
        intent_id=args.intent_id,
        plan_id=args.plan_id,
    )

    if args.dry_run:
        print(json.dumps({"status": "dry_run", "request": strategy_request_to_dict(req)}, indent=2))
        return 0

    if not private_key:
        logging.getLogger("openclaw.turnkey").error(
            "Missing private key: pass --private-key or set OPENCLAW_EXECUTION_PRIVATE_KEY (or OPENCLAW_OPERATOR_PRIVATE_KEY fallback)"
        )
        return 3

    broadcaster = TronTxBroadcaster(cfg, private_key, permission_id=permission_id)
    result = run_smart_router_strategy_swap(req, broadcaster=broadcaster, permission_id=permission_id)
    print(json.dumps({"status": "ok", "request": strategy_request_to_dict(req), "result": result}, indent=2))
    return 0 if result.get("status") in {"submitted", "confirmed"} else 3



def _derive_metrics_for_autonomous_cycle(args: argparse.Namespace) -> dict[str, float | str]:
    cfg = load_turnkey_config(args.config)
    sun_scale = 1_000_000
    jtoken_scale = 100_000_000
    account_candidates: list[tuple[str, str]] = []
    if args.owner_address:
        account_candidates.append(("owner", args.owner_address))

    mode = cfg.borrower_account_mode.strip().lower()
    if mode == "strategy_module":
        account_candidates.append(("strategy_module", cfg.addresses.strategy_module))
    elif mode == "justlend_ops_module":
        account_candidates.append(("justlend_ops_module", cfg.addresses.justlend_ops_module))
    else:
        account_candidates.append(("treasury", cfg.addresses.treasury))

    # Deduplicate while preserving order.
    unique_candidates: list[tuple[str, str]] = []
    seen_accounts: set[str] = set()
    for label, account in account_candidates:
        if account and account not in seen_accounts:
            unique_candidates.append((label, account))
            seen_accounts.add(account)

    best_metrics = {
        "observed_account": unique_candidates[0][1] if unique_candidates else "",
        "observed_source": unique_candidates[0][0] if unique_candidates else "",
        "selected_market": cfg.addresses.jtrx_market,
        "trx_exposure_total": 0.0,
        "trx_borrow_ratio": 0.0,
        "trx_collateral_ratio": 0.0,
        "trx_jtoken_balance": 0.0,
        "trx_borrow_balance": 0.0,
        "trx_collateral_underlying_balance": 0.0,
        "trx_exchange_rate": 0.0,
        "trx_exchange_rate_mantissa": 0.0,
        "trx_borrowed": 0.0,
        "jtrx_balance": 0.0,
        "jtrx_underlying": 0.0,
        "usdt_exposure_total": 0.0,
        "usdt_borrow_ratio": 0.0,
        "usdt_collateral_ratio": 0.0,
        "usdt_jtoken_balance": 0.0,
        "usdt_borrow_balance": 0.0,
        "usdt_collateral_underlying_balance": 0.0,
        "usdt_exchange_rate": 0.0,
        "usdt_exchange_rate_mantissa": 0.0,
        "usdt_borrowed": 0.0,
        "jusdt_balance": 0.0,
        "jusdt_underlying": 0.0,
        # legacy aliases used by existing tests/callers (maps to TRX metrics)
        "borrow_ratio": 0.0,
        "collateral_ratio": 0.0,
        "exposure_total": 0.0,
        "jtoken_balance": 0.0,
        "borrow_balance": 0.0,
        "collateral_underlying_balance": 0.0,
        "exchange_rate": 0.0,
        "exchange_rate_mantissa": 0.0,
    }

    for source_label, account in unique_candidates:
        payload = monitor_account_health(account, args.config)
        markets = payload.get("markets", [])
        if not markets:
            continue

        selected_market = None
        jtrx_market = cfg.addresses.jtrx_market
        market_map = {str(m.get("market", "")): m for m in markets if isinstance(m, dict)}
        for market in markets:
            if market.get("market") == jtrx_market:
                selected_market = market
                break
        if selected_market is None:
            selected_market = markets[0]

        trx_borrowed = 0.0
        jtrx_balance = 0.0
        jtrx_underlying = 0.0
        usdt_borrowed = 0.0
        jusdt_balance = 0.0
        jusdt_underlying = 0.0

        jtrx_snapshot = market_map.get(cfg.addresses.jtrx_market)
        if jtrx_snapshot:
            trx_borrowed = float(jtrx_snapshot.get("borrow_balance", 0.0) or 0.0) / sun_scale
            jtrx_balance = float(jtrx_snapshot.get("jtoken_balance", 0.0) or 0.0) / jtoken_scale
            jtrx_exchange_rate = float(jtrx_snapshot.get("exchange_rate", 0.0) or 0.0)
            jtrx_exchange_rate_mantissa = float(jtrx_snapshot.get("exchange_rate_mantissa", 0.0) or 0.0)
            if jtrx_exchange_rate > 0:
                jtrx_underlying = (float(jtrx_snapshot.get("jtoken_balance", 0.0) or 0.0) * jtrx_exchange_rate) / sun_scale
            elif jtrx_exchange_rate_mantissa > 0:
                jtrx_underlying = ((float(jtrx_snapshot.get("jtoken_balance", 0.0) or 0.0) * jtrx_exchange_rate_mantissa) / 1e18) / sun_scale

        jusdt_market = cfg.addresses.jusdt_market
        if jusdt_market:
            jusdt_snapshot = market_map.get(jusdt_market)
            if jusdt_snapshot:
                usdt_borrowed = float(jusdt_snapshot.get("borrow_balance", 0.0) or 0.0) / sun_scale
                jusdt_balance = float(jusdt_snapshot.get("jtoken_balance", 0.0) or 0.0) / jtoken_scale
                jusdt_exchange_rate = float(jusdt_snapshot.get("exchange_rate", 0.0) or 0.0)
                jusdt_exchange_rate_mantissa = float(jusdt_snapshot.get("exchange_rate_mantissa", 0.0) or 0.0)
                if jusdt_exchange_rate > 0:
                    jusdt_underlying = (float(jusdt_snapshot.get("jtoken_balance", 0.0) or 0.0) * jusdt_exchange_rate) / sun_scale
                elif jusdt_exchange_rate_mantissa > 0:
                    jusdt_underlying = ((float(jusdt_snapshot.get("jtoken_balance", 0.0) or 0.0) * jusdt_exchange_rate_mantissa) / 1e18) / sun_scale

        def _compute_market_metrics(market_snapshot: dict[str, float | int | str] | None) -> dict[str, float]:
            if not market_snapshot:
                return {
                    "jtoken_balance": 0.0,
                    "borrow_balance": 0.0,
                    "exchange_rate": 0.0,
                    "exchange_rate_mantissa": 0.0,
                    "collateral_underlying_balance": 0.0,
                    "exposure_total": 0.0,
                    "borrow_ratio": 0.0,
                    "collateral_ratio": 0.0,
                }
            jtoken_balance = float(market_snapshot.get("jtoken_balance", 0) or 0)
            borrow_balance = float(market_snapshot.get("borrow_balance", 0) or 0)
            exchange_rate = float(market_snapshot.get("exchange_rate", 0.0) or 0.0)
            exchange_rate_mantissa = float(market_snapshot.get("exchange_rate_mantissa", 0) or 0)

            collateral_underlying_balance = jtoken_balance
            if exchange_rate > 0:
                collateral_underlying_balance = jtoken_balance * exchange_rate
            elif exchange_rate_mantissa > 0:
                collateral_underlying_balance = (jtoken_balance * exchange_rate_mantissa) / 1e18

            exposure_total = collateral_underlying_balance + borrow_balance
            borrow_ratio = borrow_balance / collateral_underlying_balance if collateral_underlying_balance > 0 else 0.0
            collateral_ratio = max(0.0, 1.0 - borrow_ratio)
            return {
                "jtoken_balance": jtoken_balance,
                "borrow_balance": borrow_balance,
                "exchange_rate": exchange_rate,
                "exchange_rate_mantissa": exchange_rate_mantissa,
                "collateral_underlying_balance": collateral_underlying_balance,
                "exposure_total": exposure_total,
                "borrow_ratio": borrow_ratio,
                "collateral_ratio": collateral_ratio,
            }

        trx_metrics = _compute_market_metrics(jtrx_snapshot)
        usdt_metrics = _compute_market_metrics(market_map.get(cfg.addresses.jusdt_market))
        combined_total = trx_metrics["exposure_total"] + usdt_metrics["exposure_total"]
        if combined_total <= (best_metrics["trx_exposure_total"] + best_metrics["usdt_exposure_total"]):
            continue

        best_metrics = {
            "observed_account": account,
            "observed_source": source_label,
            "selected_market": selected_market.get("market", ""),
            "trx_exposure_total": trx_metrics["exposure_total"],
            "trx_borrow_ratio": trx_metrics["borrow_ratio"],
            "trx_collateral_ratio": trx_metrics["collateral_ratio"],
            "trx_jtoken_balance": trx_metrics["jtoken_balance"],
            "trx_borrow_balance": trx_metrics["borrow_balance"],
            "trx_collateral_underlying_balance": trx_metrics["collateral_underlying_balance"],
            "trx_exchange_rate": trx_metrics["exchange_rate"],
            "trx_exchange_rate_mantissa": trx_metrics["exchange_rate_mantissa"],
            "trx_borrowed": trx_borrowed,
            "jtrx_balance": jtrx_balance,
            "jtrx_underlying": jtrx_underlying,
            "usdt_exposure_total": usdt_metrics["exposure_total"],
            "usdt_borrow_ratio": usdt_metrics["borrow_ratio"],
            "usdt_collateral_ratio": usdt_metrics["collateral_ratio"],
            "usdt_jtoken_balance": usdt_metrics["jtoken_balance"],
            "usdt_borrow_balance": usdt_metrics["borrow_balance"],
            "usdt_collateral_underlying_balance": usdt_metrics["collateral_underlying_balance"],
            "usdt_exchange_rate": usdt_metrics["exchange_rate"],
            "usdt_exchange_rate_mantissa": usdt_metrics["exchange_rate_mantissa"],
            "usdt_borrowed": usdt_borrowed,
            "jusdt_balance": jusdt_balance,
            "jusdt_underlying": jusdt_underlying,
            # legacy aliases map to TRX
            "borrow_ratio": trx_metrics["borrow_ratio"],
            "collateral_ratio": trx_metrics["collateral_ratio"],
            "exposure_total": trx_metrics["exposure_total"],
            "jtoken_balance": trx_metrics["jtoken_balance"],
            "borrow_balance": trx_metrics["borrow_balance"],
            "collateral_underlying_balance": trx_metrics["collateral_underlying_balance"],
            "exchange_rate": trx_metrics["exchange_rate"],
            "exchange_rate_mantissa": trx_metrics["exchange_rate_mantissa"],
        }

    return best_metrics


def _rpc_post_json(rpc_url: str, path: str, payload: dict[str, object]) -> dict[str, object]:
    req = urllib.request.Request(
        f"{rpc_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _read_trx_balance_sun(rpc_url: str, account: str) -> int:
    payload = _rpc_post_json(rpc_url, "/wallet/getaccount", {"address": account, "visible": True})
    return int(payload.get("balance", 0) or 0)


def _read_trc20_balance(rpc_url: str, token_address: str, owner: str) -> int:
    payload = {
        "owner_address": owner,
        "contract_address": token_address,
        "function_selector": "balanceOf(address)",
        "parameter": _encode_types(["address"], [owner]),
        "visible": True,
    }
    body = _rpc_post_json(rpc_url, "/wallet/triggerconstantcontract", payload)
    const = body.get("constant_result", [])
    if not const:
        return 0
    return int(str(const[0]).replace("0x", "") or "0", 16)


def maybe_sweep_ops_to_treasury(
    *,
    cfg,
    owner_address: str,
    threshold_ratio: float,
    broadcaster: TronTxBroadcaster | None,
    log: logging.Logger,
) -> dict[str, object]:
    if threshold_ratio <= 0:
        return {"enabled": False, "reason": "threshold_ratio_non_positive"}
    if broadcaster is None:
        return {"enabled": False, "reason": "broadcast_disabled"}

    ops = cfg.addresses.justlend_ops_module
    treasury = cfg.addresses.treasury
    usdt_address = cfg.allowed_assets.get("USDT", "")

    ops_trx = _read_trx_balance_sun(cfg.rpc_url, ops)
    treasury_trx = _read_trx_balance_sun(cfg.rpc_url, treasury)
    ops_usdt = _read_trc20_balance(cfg.rpc_url, usdt_address, ops) if usdt_address else 0
    treasury_usdt = _read_trc20_balance(cfg.rpc_url, usdt_address, treasury) if usdt_address else 0

    trx_threshold = int(treasury_trx * threshold_ratio)
    usdt_threshold = int(treasury_usdt * threshold_ratio)

    decisions = {
        "enabled": True,
        "threshold_ratio": threshold_ratio,
        "ops_trx_sun": ops_trx,
        "treasury_trx_sun": treasury_trx,
        "trx_threshold_sun": trx_threshold,
        "ops_usdt": ops_usdt,
        "treasury_usdt": treasury_usdt,
        "usdt_threshold": usdt_threshold,
        "swept": [],
    }

    should_sweep_trx = treasury_trx > 0 and ops_trx > trx_threshold
    should_sweep_usdt = treasury_usdt > 0 and ops_usdt > usdt_threshold

    if should_sweep_trx:
        result = broadcaster.broadcast_ops_sweep_trx(owner_address=owner_address, ops_module=ops)
        decisions["swept"].append({"asset": "TRX", "txid": result.txid})
        log.info("Auto-swept ops TRX to treasury txid=%s", result.txid)

    if should_sweep_usdt:
        if usdt_address:
            result = broadcaster.broadcast_ops_sweep_token(owner_address=owner_address, ops_module=ops, token_address=usdt_address)
            decisions["swept"].append({"asset": "USDT", "txid": result.txid})
            log.info("Auto-swept ops USDT to treasury txid=%s", result.txid)

    return decisions


def _run_autonomous_jtrx(args: argparse.Namespace) -> int:
    actions = parse_action_sequence(args.action_sequence)
    usdt_actions = parse_action_sequence(getattr(args, "usdt_action_sequence", args.action_sequence))
    cfg = load_turnkey_config(args.config)
    if not hasattr(args, "usdt_amount_sun"):
        args.usdt_amount_sun = None
    if not hasattr(args, "usdt_market"):
        args.usdt_market = ""
    if not hasattr(args, "usdt_underlying_asset"):
        args.usdt_underlying_asset = "USDT"
    if not hasattr(args, "usdt_repay_threshold"):
        args.usdt_repay_threshold = args.repay_threshold
    if not hasattr(args, "usdt_borrow_threshold"):
        args.usdt_borrow_threshold = args.borrow_threshold
    if not hasattr(args, "usdt_repay_collateral_ratio_below"):
        args.usdt_repay_collateral_ratio_below = args.repay_collateral_ratio_below
    if not hasattr(args, "usdt_borrow_collateral_ratio_above"):
        args.usdt_borrow_collateral_ratio_above = args.borrow_collateral_ratio_above
    if not hasattr(args, "broadcast"):
        args.broadcast = False
    if not hasattr(args, "wait_for_receipt"):
        args.wait_for_receipt = False
    if not hasattr(args, "owner_address"):
        args.owner_address = ""
    if not hasattr(args, "private_key"):
        args.private_key = ""
    if not hasattr(args, "signer_role"):
        args.signer_role = SIGNER_ROLE_EXECUTION
    if not hasattr(args, "permission_id"):
        args.permission_id = None
    if not hasattr(args, "ops_sweep_threshold_ratio"):
        args.ops_sweep_threshold_ratio = 0.10
    if args.usdt_amount_sun is None:
        args.usdt_amount_sun = args.amount_sun
    if not args.usdt_market:
        args.usdt_market = cfg.addresses.jusdt_market
    log = logging.getLogger("openclaw.turnkey")

    adaptive_mode = (args.strategy_mode or "fixed").strip().lower() == "adaptive"
    concise_output = getattr(args, "concise_output", False)

    owner = args.owner_address or getattr(cfg.addresses, "operator", "")
    permission_id = args.permission_id if args.permission_id is not None else getattr(cfg, "permission_id", 0)
    signing = resolve_signing_context(signer_role=args.signer_role, explicit_private_key=args.private_key)
    sweep_broadcaster = TronTxBroadcaster(cfg, signing.private_key_hex, permission_id=permission_id) if (args.broadcast and signing.private_key_hex) else None

    log.info(
        "Adaptive two-cycle example:\n"
        "cycle=0 | TRX action=mint | USDT action=borrow | trx_borrow_ratio=28.00%% | usdt_borrow_ratio=22.00%%\n"
        "cycle=1 | TRX action=borrow | USDT action=repayBorrow | trx_borrow_ratio=18.00%% | usdt_borrow_ratio=64.00%%"
    )

    current_usdt_action = usdt_actions[0]

    def _cycle_runner(plan):
        if not concise_output:
            log.info("Autonomous cycle start index=%s action=%s intent_id=%s", plan.cycle_index, plan.action, plan.intent_id)
        cycle_args = argparse.Namespace(**vars(args))
        cycle_args.intent_id = plan.intent_id
        cycle_args.action = plan.action
        cycle_args.compact_output = True
        rc = _run_jtrx(cycle_args)
        usdt_intent_id = f"{plan.intent_id}-usdt"
        usdt_cycle_args = argparse.Namespace(**vars(args))
        usdt_cycle_args.intent_id = usdt_intent_id
        usdt_cycle_args.action = current_usdt_action
        usdt_cycle_args.compact_output = True
        usdt_cycle_args.amount_sun = args.usdt_amount_sun
        usdt_cycle_args.market = args.usdt_market or ""
        usdt_cycle_args.underlying_asset = args.usdt_underlying_asset
        rc_usdt = _run_jtrx(usdt_cycle_args)
        rc = rc if rc != 0 else rc_usdt

        try:
            sweep_summary = maybe_sweep_ops_to_treasury(
                cfg=cfg,
                owner_address=owner,
                threshold_ratio=float(args.ops_sweep_threshold_ratio),
                broadcaster=sweep_broadcaster,
                log=log,
            )
            if not concise_output:
                log.info("Ops auto-sweep summary: %s", json.dumps(sweep_summary, default=str))
        except Exception as exc:
            log.error("Ops auto-sweep failed: %s", exc)
            rc = rc if rc != 0 else 4

        if not concise_output:
            log.info("Autonomous cycle end index=%s status=%s", plan.cycle_index, "ok" if rc == 0 else f"failed:{rc}")
        log.info("======")
        return rc

    action_selector = None
    if adaptive_mode:
        sun_scale = 1_000_000
        jtoken_scale = 100_000_000

        def _action_selector(_cycle_index: int) -> str:
            nonlocal current_usdt_action
            metrics = _derive_metrics_for_autonomous_cycle(args)
            selected_trx = decide_action_from_metrics(
                metrics=metrics,
                repay_threshold=args.repay_threshold,
                borrow_threshold=args.borrow_threshold,
                repay_collateral_ratio_below=args.repay_collateral_ratio_below,
                borrow_collateral_ratio_above=args.borrow_collateral_ratio_above,
            )
            usdt_metric_scope = {
                "borrow_ratio": float(metrics.get("usdt_borrow_ratio", 0.0) or 0.0),
                "collateral_ratio": float(metrics.get("usdt_collateral_ratio", 0.0) or 0.0),
            }
            selected_usdt = decide_action_from_metrics(
                metrics=usdt_metric_scope,
                repay_threshold=args.usdt_repay_threshold,
                borrow_threshold=args.usdt_borrow_threshold,
                repay_collateral_ratio_below=args.usdt_repay_collateral_ratio_below,
                borrow_collateral_ratio_above=args.usdt_borrow_collateral_ratio_above,
            )
            effective_repay_threshold, effective_borrow_threshold = resolve_effective_thresholds(
                repay_threshold=args.repay_threshold,
                borrow_threshold=args.borrow_threshold,
                repay_collateral_ratio_below=args.repay_collateral_ratio_below,
                borrow_collateral_ratio_above=args.borrow_collateral_ratio_above,
            )
            usdt_effective_repay_threshold, usdt_effective_borrow_threshold = resolve_effective_thresholds(
                repay_threshold=args.usdt_repay_threshold,
                borrow_threshold=args.usdt_borrow_threshold,
                repay_collateral_ratio_below=args.usdt_repay_collateral_ratio_below,
                borrow_collateral_ratio_above=args.usdt_borrow_collateral_ratio_above,
            )
            trx_borrow_ratio = float(metrics.get("trx_borrow_ratio", 0.0) or 0.0)
            trx_collateral_ratio = float(metrics.get("trx_collateral_ratio", 0.0) or 0.0)
            usdt_borrow_ratio = float(metrics.get("usdt_borrow_ratio", 0.0) or 0.0)
            usdt_collateral_ratio = float(metrics.get("usdt_collateral_ratio", 0.0) or 0.0)
            if concise_output:
                trx_action_label = str(selected_trx).upper()
                usdt_action_label = str(selected_usdt).upper()
                log.info(
                    (
                        "Adaptive cycle metrics | trx_action=%s / usdt_action=%s / intent_prefix=%s / account=%s / \n"
                        "---------------------------\n"
                        "> TRX Borrow Ratio =%.2f%% / TRX Collateral Ratio =%.2f%%\n"
                        "> USDT Borrow Ratio =%.2f%% / USDT Collateral Ratio =%.2f%%\n"
                        "> TRX borrowed = %.2f\n"
                        "> jTRX balance = %.2f (%.2f TRX)\n"
                        "> USDT borrowed = %.2f\n"
                        "> jUSDT balance = %.2f (%.2f USDT)\n"
                        "----------------------------"
                    ),
                    trx_action_label,
                    usdt_action_label,
                    args.intent_prefix,
                    metrics.get("observed_account", ""),
                    trx_borrow_ratio * 100,
                    trx_collateral_ratio * 100,
                    usdt_borrow_ratio * 100,
                    usdt_collateral_ratio * 100,
                    float(metrics.get("trx_borrowed", 0.0) or 0.0),
                    float(metrics.get("jtrx_balance", 0.0) or 0.0),
                    float(metrics.get("jtrx_underlying", 0.0) or 0.0),
                    float(metrics.get("usdt_borrowed", 0.0) or 0.0),
                    float(metrics.get("jusdt_balance", 0.0) or 0.0),
                    float(metrics.get("jusdt_underlying", 0.0) or 0.0),
                )
            else:
                log.info(
                    (
                        "Adaptive selector TRX status\n"
                        "> observed_source=%s\n"
                        "> observed_account=%s\n"
                        "> jtoken_balance=%.10f\n"
                        "> collateral_underlying_balance=%.12f\n"
                        "> borrow_balance=%.12f\n"
                        "> exchange_rate=%.12f\n"
                        "> exchange_rate_mantissa=%.0f\n"
                        "> exposure_total=%.12f"
                    ),
                    metrics.get("observed_source", ""),
                    metrics.get("observed_account", ""),
                    float(metrics.get("trx_jtoken_balance", 0.0) or 0.0) / jtoken_scale,
                    float(metrics.get("trx_collateral_underlying_balance", 0.0) or 0.0) / sun_scale,
                    float(metrics.get("trx_borrow_balance", 0.0) or 0.0) / sun_scale,
                    metrics.get("trx_exchange_rate", 0.0),
                    metrics.get("trx_exchange_rate_mantissa", 0.0),
                    float(metrics.get("trx_exposure_total", 0.0) or 0.0) / sun_scale,
                )
                log.info(
                    (
                        "Adaptive selector USDT status\n"
                        "> jtoken_balance=%.10f\n"
                        "> collateral_underlying_balance=%.12f\n"
                        "> borrow_balance=%.12f\n"
                        "> exchange_rate=%.12f\n"
                        "> exchange_rate_mantissa=%.0f\n"
                        "> exposure_total=%.12f"
                    ),
                    float(metrics.get("usdt_jtoken_balance", 0.0) or 0.0) / jtoken_scale,
                    float(metrics.get("usdt_collateral_underlying_balance", 0.0) or 0.0) / sun_scale,
                    float(metrics.get("usdt_borrow_balance", 0.0) or 0.0) / sun_scale,
                    metrics.get("usdt_exchange_rate", 0.0),
                    metrics.get("usdt_exchange_rate_mantissa", 0.0),
                    float(metrics.get("usdt_exposure_total", 0.0) or 0.0) / sun_scale,
                )
                log.info(
                    (
                        "Adaptive selector thresholds:\n"
                        "> trx_borrow_ratio=(%.2f%%)\n"
                        "> trx_collateral_ratio=(%.2f%%)\n"
                        "> usdt_borrow_ratio=(%.2f%%)\n"
                        "> usdt_collateral_ratio=(%.2f%%)\n"
                        "> trx_effective_repay_threshold=(%.2f%%)\n"
                        "> trx_effective_borrow_threshold=(%.2f%%)\n"
                        "> usdt_effective_repay_threshold=(%.2f%%)\n"
                        "> usdt_effective_borrow_threshold=(%.2f%%)\n"
                        "> repay_collateral_ratio_below=%s\n"
                        "> borrow_collateral_ratio_above=%s\n"
                        "> usdt_repay_collateral_ratio_below=%s\n"
                        "> usdt_borrow_collateral_ratio_above=%s"
                    ),
                    trx_borrow_ratio * 100,
                    trx_collateral_ratio * 100,
                    usdt_borrow_ratio * 100,
                    usdt_collateral_ratio * 100,
                    effective_repay_threshold * 100,
                    effective_borrow_threshold * 100,
                    usdt_effective_repay_threshold * 100,
                    usdt_effective_borrow_threshold * 100,
                    args.repay_collateral_ratio_below,
                    args.borrow_collateral_ratio_above,
                    args.usdt_repay_collateral_ratio_below,
                    args.usdt_borrow_collateral_ratio_above,
                )
                log.info(
                    "Adaptive selector config\n> trx_base_repay_threshold=%.6f\n> trx_base_borrow_threshold=%.6f\n> usdt_base_repay_threshold=%.6f\n> usdt_base_borrow_threshold=%.6f",
                    args.repay_threshold,
                    args.borrow_threshold,
                    args.usdt_repay_threshold,
                    args.usdt_borrow_threshold,
                )
                log.info(
                    (
                        "Adaptive selector decision:\n"
                        "> selected_trx_action=%s\n"
                        "> selected_usdt_action=%s\n"
                        "> observed_account=%s\n"
                        "> trx_borrow_ratio=(%.2f%%)\n"
                        "> trx_collateral_ratio=(%.2f%%)\n"
                        "> usdt_borrow_ratio=(%.2f%%)\n"
                        "> usdt_collateral_ratio=(%.2f%%)"
                    ),
                    selected_trx,
                    selected_usdt,
                    metrics.get("observed_account", ""),
                    trx_borrow_ratio * 100,
                    trx_collateral_ratio * 100,
                    usdt_borrow_ratio * 100,
                    usdt_collateral_ratio * 100,
                )
            current_usdt_action = selected_usdt
            return selected_trx

        action_selector = _action_selector
    else:
        def _fixed_action_selector(cycle_index: int) -> str:
            nonlocal current_usdt_action
            current_usdt_action = usdt_actions[cycle_index % len(usdt_actions)]
            return actions[cycle_index % len(actions)]

        action_selector = _fixed_action_selector

    summary = run_autonomous_cycles(
        actions=actions,
        intent_prefix=args.intent_prefix,
        max_cycles=args.max_cycles,
        interval_s=args.interval_s,
        stop_on_failure=args.stop_on_failure,
        cycle_runner=_cycle_runner,
        action_selector=action_selector,
    )
    print(json.dumps({
        "status": "completed",
        "strategy_mode": "adaptive" if adaptive_mode else "fixed",
        "total_cycles": summary.total_cycles,
        "succeeded_cycles": summary.succeeded_cycles,
        "failed_cycles": summary.failed_cycles,
        "interrupted": summary.interrupted,
    }, indent=2))
    return 0 if summary.failed_cycles == 0 else 3


def _run_strategy_trx(args: argparse.Namespace) -> int:
    cfg = load_turnkey_config(args.config)
    owner = args.owner_address or getattr(cfg.addresses, "operator", "")
    permission_id = args.permission_id if args.permission_id is not None else getattr(cfg, "permission_id", 0)
    signing = resolve_signing_context(signer_role=args.signer_role, explicit_private_key=args.private_key)
    private_key = signing.private_key_hex

    if not args.broadcast:
        print(json.dumps({
            "status": "simulated",
            "note": "Use --broadcast to submit executeCallWithTRX",
            "request": {
                "intent_id": args.intent_id,
                "plan_id": args.plan_id,
                "amount_sun": args.amount_sun,
                "target": args.target,
                "call_data": args.call_data,
            },
        }, indent=2))
        return 0

    if not private_key:
        logging.getLogger("openclaw.turnkey").error(
            "Missing private key: pass --private-key or set OPENCLAW_EXECUTION_PRIVATE_KEY (or OPENCLAW_OPERATOR_PRIVATE_KEY fallback)"
        )
        return 3

    broadcaster = TronTxBroadcaster(cfg, private_key, permission_id=permission_id)
    result = broadcaster.broadcast_execute_call_with_trx(
        owner_address=owner,
        strategy_module=(args.strategy_module or cfg.addresses.strategy_module),
        intent_id=args.intent_id,
        plan_id=args.plan_id,
        amount_sun=args.amount_sun,
        target=args.target,
        call_data_hex=args.call_data,
    )

    output = {
        "status": "submitted",
        "txid": result.txid,
        "response": result.raw_response,
    }

    if args.wait_for_receipt:
        receipt = broadcaster.wait_for_receipt(result.txid)
        receipt_result = receipt.get("receipt", {}).get("result")
        output["status"] = "confirmed" if receipt_result == "SUCCESS" else "failed"
        output["receipt"] = receipt

    print(json.dumps(output, indent=2))
    return 0 if output["status"] in {"submitted", "confirmed"} else 3


def _run_jtrx(args: argparse.Namespace) -> int:
    cfg = load_turnkey_config(args.config)
    owner = args.owner_address or getattr(cfg.addresses, "operator", "")
    permission_id = args.permission_id if args.permission_id is not None else getattr(cfg, "permission_id", 0)
    signing = resolve_signing_context(signer_role=args.signer_role, explicit_private_key=args.private_key)
    private_key = signing.private_key_hex

    log = logging.getLogger("openclaw.turnkey")
    concise_output = getattr(args, "concise_output", False)

    if not concise_output:
        log.info("Starting jTRX agent run")
        log.info("Operator/owner address: %s", owner)
        log.info("Strategy module: %s", cfg.addresses.strategy_module)
        log.info("JustLend ops module: %s", cfg.addresses.justlend_ops_module)
        log.info("jTRX market: %s", cfg.addresses.jtrx_market)
        if args.market:
            log.info("Override market: %s", args.market)
        if args.underlying_asset:
            log.info("Underlying asset: %s", args.underlying_asset)

    normalized_underlying_asset = _normalize_underlying_asset(args.underlying_asset, cfg.allowed_assets)

    decision_engine = JTRXDecisionEngine()
    intent = JTRXDecisionIntent(
        intent_id=args.intent_id,
        action=normalize_jtrx_action(args.action),
        amount_sun=args.amount_sun,
        requested_size_nav_pct=args.requested_size_nav_pct,
        daily_cumulative_nav_pct=args.daily_cumulative_nav_pct,
        strategy_amount_sun=args.strategy_amount_sun,
        market=args.market,
        underlying_asset=normalized_underlying_asset,
    )

    if not concise_output:
        log.info("Evaluating intent")
    decision = decision_engine.evaluate(intent, degraded_data=args.degraded_data, emergency=args.emergency)
    if not concise_output:
        log.info("Policy decision: allowed=%s tier=%s reasons=%s", decision.allowed, decision.approval_tier.value, decision.denial_reasons)
    if not decision.allowed:
        print(json.dumps({"intent": intent.__dict__, "decision": decision.__dict__}, indent=2, default=str))
        return 2

    if not concise_output:
        log.info("Building execution plan")
    plan = decision_engine.build_plan(intent, decision, cfg, now_epoch_s=int(time.time()), ttl_s=args.ttl_s)
    if not concise_output:
        log.info("Plan id=%s required_approvals=%s", plan.execution_plan.plan_id, plan.execution_plan.required_approvals)

    approvals = [x.strip() for x in args.approved_by.split(",") if x.strip()]
    identities = parse_approval_identities(args.approval_identities)
    approval_artifacts = build_approval_artifacts(approvals, identities)
    broadcaster = TronTxBroadcaster(cfg, private_key, permission_id=permission_id) if (args.broadcast and private_key) else None

    indexer = build_indexer(cfg)

    def _observer(receipt):
        emit_execution_event_safe(
            indexer,
            {
                "event": "jtrx_execution_receipt",
                "network": cfg.network,
                "chain_id": cfg.chain_id,
                "owner": owner,
                "plan_id": receipt.plan_id,
                "status": receipt.status,
                "tx_hashes": receipt.tx_hashes,
                "failure_reason": receipt.failure_reason,
                "metadata": receipt.metadata,
            },
        )

    executor = JTRXExecutor(
        broadcaster=broadcaster,
        owner_address=owner,
        signer_label=signing.signer_label,
        signer_provenance={
            "signer_role": signing.signer_role,
            "signer_source": signing.signer_source,
            "signer_key_id": signing.signer_key_id,
        },
        execution_observer=_observer,
    )

    if not concise_output:
        log.info("Executing plan broadcast=%s wait_for_receipt=%s", args.broadcast, args.wait_for_receipt)
    receipt = executor.execute(
        plan,
        approved_by=approvals,
        broadcast=args.broadcast,
        wait_for_receipt=args.wait_for_receipt,
    )
    if concise_output:
        tx_hashes = ",".join(receipt.tx_hashes)
        failure_reason = receipt.failure_reason or "/"
        log.info(
            "Execution summary | action=%s / intent_id=%s / status=%s / tx_hashes=%s / failure_reason=%s /",
            intent.action.upper(),
            intent.intent_id,
            receipt.status.upper(),
            tx_hashes,
            failure_reason,
        )
    else:
        log.info("Execution result status=%s tx_hashes=%s failure=%s", receipt.status, receipt.tx_hashes, receipt.failure_reason)

    intent_payload = intent.__dict__
    decision_payload = decision.__dict__
    plan_payload = {
        "policy_decision": plan.policy_decision.__dict__,
        "execution_plan": {
            "intent_id": plan.execution_plan.intent_id,
            "plan_id": plan.execution_plan.plan_id,
            "ordered_steps": [
                {"name": s.name, "params": s.params} for s in plan.execution_plan.ordered_steps
            ],
            "max_slippage_bps": plan.execution_plan.max_slippage_bps,
            "deadline_epoch_s": plan.execution_plan.deadline_epoch_s,
            "required_approvals": plan.execution_plan.required_approvals,
        },
    }
    receipt_payload = {
        "plan_id": receipt.plan_id,
        "tx_hashes": receipt.tx_hashes,
        "status": receipt.status,
        "resource_usage": receipt.resource_usage,
        "failure_reason": receipt.failure_reason,
        "metadata": receipt.metadata,
    }
    audit_bundle = build_audit_bundle(
        network=cfg.network,
        chain_id=cfg.chain_id,
        owner=owner,
        intent=intent_payload,
        decision=decision_payload,
        plan=plan_payload,
        receipt=receipt_payload,
        approval_artifacts=approval_artifacts,
    )
    emit_execution_event_safe(
        indexer,
        {
            "event": "jtrx_audit_bundle",
            "audit_bundle_id": audit_bundle["audit_bundle_id"],
            "audit_bundle_hash": audit_bundle["audit_bundle_hash"],
            "network": cfg.network,
            "chain_id": cfg.chain_id,
            "owner": owner,
            "plan_id": plan.execution_plan.plan_id,
            "intent_id": intent.intent_id,
            "action": intent.action,
            "status": receipt.status,
            "approval_tier": decision.approval_tier.value,
            "approval_artifacts": approval_artifacts,
        },
    )

    payload = {
        "intent": intent_payload,
        "decision": decision_payload,
        "plan": plan_payload,
        "receipt": receipt_payload,
        "approval_artifacts": approval_artifacts,
        "audit_bundle": audit_bundle,
    }
    if concise_output:
        pass
    elif getattr(args, "compact_output", False):
        compact = {
            "intent_id": intent.intent_id,
            "action": intent.action,
            "status": receipt.status,
            "plan_id": receipt.plan_id,
            "tx_hashes": receipt.tx_hashes,
            "failure_reason": receipt.failure_reason,
        }
        print(json.dumps(compact, default=str))
    else:
        print(json.dumps(payload, indent=2, default=str))

    return 0 if receipt.status in {"simulated", "submitted", "confirmed"} else 3




def _normalize_underlying_asset(raw: str, allowed_assets: dict[str, str]) -> str:
    value = (raw or "").strip()
    if not value:
        return ""

    for symbol, address in allowed_assets.items():
        if value.lower() == address.lower():
            return symbol
    return value

def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw turnkey jTRX agent")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logs")

    sub = parser.add_subparsers(dest="command", required=True)

    p_health = sub.add_parser("health", help="Run RPC and JustLend health checks")
    p_health.add_argument("--config", default=None, help="Path to turnkey config JSON")

    p_monitor = sub.add_parser("monitor-account", help="Fetch read-only JustLend market snapshots for an account")
    p_monitor.add_argument("--config", default=None, help="Path to turnkey config JSON")
    p_monitor.add_argument("--owner-address", default="", help="TRON account address to inspect (defaults to treasury address from config)")

    p_ready = sub.add_parser("check-borrow-ready", help="Verify read-only module/jTRX allowlist readiness for borrow+repay")
    p_ready.add_argument("--config", default=None, help="Path to turnkey config JSON")

    p_setup_mcp = sub.add_parser("setup-mcp-testnet", help="Generate Nile MCP server setup plan")
    p_setup_mcp.add_argument("--config", default=None, help="Path to turnkey config JSON")
    p_setup_mcp.add_argument("--mcp-server-tron-url", default="http://127.0.0.1:3000", help="MCP server base URL")
    p_setup_mcp.add_argument("--subgraph-indexer-url", default="", help="Optional subgraph/indexer endpoint")

    p_search = sub.add_parser("search-reviewer-operator", help="Search reviewer/operator decision artifacts from indexed events")
    p_search.add_argument("--config", default=None, help="Path to turnkey config JSON")
    p_search.add_argument("--query", default="", help="Text query (intent id, plan id, owner, approver, etc.)")
    p_search.add_argument("--role", default="all", choices=["all", "reviewer", "operator"], help="Filter by approval role")
    p_search.add_argument("--limit", type=int, default=20, help="Maximum hits to return")

    p_package = sub.add_parser("package-openclaw-extension", help="Package BankofAI-standard OpenClaw extension + skills bundle")
    p_package.add_argument("--output-dir", default="openclaw_agent/dist/openclaw-extension", help="Output directory for packaged extension assets")

    p_auto = sub.add_parser("run-autonomous-jtrx", help="Run autonomous multi-cycle jTRX execution loop")
    p_auto.add_argument("--config", default=None, help="Path to turnkey config JSON")
    p_auto.add_argument("--action-sequence", default="mint,borrow,repayBorrow,redeemUnderlying", help="Comma-separated TRX/jTRX actions to rotate")
    p_auto.add_argument("--usdt-action-sequence", default="mint,borrow,repayBorrow,redeemUnderlying", help="Comma-separated USDT/jUSDT actions to rotate independently")
    p_auto.add_argument("--strategy-mode", default="fixed", choices=["fixed", "adaptive"], help="Action selection mode: fixed cycle or adaptive metrics-driven")
    p_auto.add_argument("--repay-threshold", type=float, default=0.6, help="Adaptive mode (TRX): repay when borrow_ratio is >= this threshold")
    p_auto.add_argument("--borrow-threshold", type=float, default=0.3, help="Adaptive mode (TRX): allow borrow when borrow_ratio is <= this threshold")
    p_auto.add_argument("--repay-collateral-ratio-below", type=float, default=None, help="Adaptive mode (TRX optional): repay when collateral_ratio falls below this value")
    p_auto.add_argument("--borrow-collateral-ratio-above", type=float, default=None, help="Adaptive mode (TRX optional): allow borrow when collateral_ratio is above this value")
    p_auto.add_argument("--usdt-repay-threshold", type=float, default=0.6, help="Adaptive mode (USDT): repay when borrow_ratio is >= this threshold")
    p_auto.add_argument("--usdt-borrow-threshold", type=float, default=0.3, help="Adaptive mode (USDT): allow borrow when borrow_ratio is <= this threshold")
    p_auto.add_argument("--usdt-repay-collateral-ratio-below", type=float, default=None, help="Adaptive mode (USDT optional): repay when collateral_ratio falls below this value")
    p_auto.add_argument("--usdt-borrow-collateral-ratio-above", type=float, default=None, help="Adaptive mode (USDT optional): allow borrow when collateral_ratio is above this value")
    p_auto.add_argument("--max-cycles", type=int, default=10, help="Maximum execution cycles")
    p_auto.add_argument("--interval-s", type=float, default=30.0, help="Delay between cycles in seconds")
    p_auto.add_argument("--intent-prefix", default="auto-jtrx", help="Intent id prefix for generated cycle intents")
    p_auto.add_argument("--amount-sun", type=int, required=True, help="TRX/jTRX cycle amount in SUN")
    p_auto.add_argument("--usdt-amount-sun", type=int, default=None, help="USDT/jUSDT cycle amount in SUN-like token decimals (defaults to --amount-sun)")
    p_auto.add_argument("--strategy-amount-sun", type=int, default=None, help="Optional strategy transfer amount override")
    p_auto.add_argument("--market", default="", help="Optional TRX market override (defaults to config jTRX market)")
    p_auto.add_argument("--underlying-asset", default="", help="TRX cycle underlying asset symbol/address for token market mint/repay")
    p_auto.add_argument("--usdt-market", default="", help="Optional USDT market override (defaults to config jUSDT market)")
    p_auto.add_argument("--usdt-underlying-asset", default="USDT", help="USDT cycle underlying asset symbol/address for token market mint/repay")
    p_auto.add_argument("--requested-size-nav-pct", type=float, required=True)
    p_auto.add_argument("--daily-cumulative-nav-pct", type=float, required=True)
    p_auto.add_argument("--approved-by", default="", help="Comma-separated approver IDs")
    p_auto.add_argument("--approval-identities", default="", help="Comma-separated approval identity entries: approver_id|trc8004_subject|role")
    p_auto.add_argument("--degraded-data", action="store_true")
    p_auto.add_argument("--emergency", action="store_true")
    p_auto.add_argument("--ttl-s", type=int, default=300)
    p_auto.add_argument("--broadcast", action="store_true", help="Broadcast transactions to blockchain")
    p_auto.add_argument("--wait-for-receipt", action="store_true", help="Poll for transaction receipt")
    p_auto.add_argument("--owner-address", default="", help="TRON owner/operator address")
    p_auto.add_argument("--private-key", default="", help="Signer private key hex")
    p_auto.add_argument("--signer-role", default=SIGNER_ROLE_EXECUTION, choices=["execution", "emergency", "analytics"], help="Signer wallet role")
    p_auto.add_argument("--permission-id", type=int, default=None, help="TRON permission id for Active Permission signing (0 uses owner/default)")
    p_auto.add_argument("--stop-on-failure", action="store_true", help="Stop loop after the first failed cycle")
    p_auto.add_argument("--concise-output", action="store_true", help="Reduce logs to key metrics and execution summary")
    p_auto.add_argument("--ops-sweep-threshold-ratio", type=float, default=0.10, help="Auto-sweep ops TRX/USDT to treasury when ops balance exceeds this ratio of treasury balance (default 0.10)")

    p_swap = sub.add_parser("run-smart-router-swap", help="Execute SunSwap Smart Router swapExactInput with deterministic route args")
    p_swap.add_argument("--config", default=None, help="Path to turnkey config JSON")
    p_swap.add_argument("--full-host", default="https://api.nileex.io", help="TRON full node URL")
    p_swap.add_argument("--smart-router", required=True, help="Smart Router contract address")
    p_swap.add_argument("--token-in", required=True, help="Input token address")
    p_swap.add_argument("--token-out", required=True, help="Output token address")
    p_swap.add_argument("--amount-in", type=int, required=True, help="Input amount in raw token units")
    p_swap.add_argument("--amount-out-min", type=int, default=1, help="Minimum output amount in raw units")
    p_swap.add_argument("--to-address", required=True, help="Recipient address")
    p_swap.add_argument("--deadline", type=int, required=True, help="Deadline unix timestamp (seconds)")
    p_swap.add_argument("--pool-version", default="v2", help="Comma-separated pool versions")
    p_swap.add_argument("--version-len", default="2", help="Comma-separated versionLen values")
    p_swap.add_argument("--fees", default="0,0", help="Comma-separated fee list, last value must be zero")
    p_swap.add_argument("--fee-limit", type=int, default=500_000_000)
    p_swap.add_argument("--auto-approve", action="store_true", help="Auto-approve tokenIn to router if allowance is insufficient")
    p_swap.add_argument("--wait-for-receipt", action="store_true", help="Wait for on-chain receipt and fail fast when swap reverts")
    p_swap.add_argument("--receipt-timeout-s", type=int, default=90, help="Receipt polling timeout in seconds when --wait-for-receipt is enabled")
    p_swap.add_argument("--auto-amount-out-min", action="store_true", help="Quote expected output via constant call and auto-set amountOutMin with slippage buffer")
    p_swap.add_argument("--quote-slippage-bps", type=int, default=100, help="Slippage buffer (in bps) applied when --auto-amount-out-min is used")
    p_swap.add_argument("--dry-run", action="store_true", help="Print normalized request without broadcasting")

    p_swap_strategy = sub.add_parser(
        "run-smart-router-strategy-swap",
        help="Execute Smart Router swap via strategy executeCallWithToken using treasury asset funds",
    )
    p_swap_strategy.add_argument("--config", default=None, help="Path to turnkey config JSON")
    p_swap_strategy.add_argument("--full-host", default="https://api.nileex.io", help="TRON full node URL")
    p_swap_strategy.add_argument("--smart-router", required=True, help="Smart Router contract address")
    p_swap_strategy.add_argument("--token-in", required=True, help="Input token address (treasury asset)")
    p_swap_strategy.add_argument("--token-out", required=True, help="Output token address")
    p_swap_strategy.add_argument("--amount-in", type=int, required=True, help="Input amount in raw token units")
    p_swap_strategy.add_argument("--amount-out-min", type=int, default=1, help="Minimum output amount in raw units")
    p_swap_strategy.add_argument("--to-address", required=True, help="Recipient address")
    p_swap_strategy.add_argument("--deadline", type=int, required=True, help="Deadline unix timestamp (seconds)")
    p_swap_strategy.add_argument("--pool-version", default="v2", help="Comma-separated pool versions")
    p_swap_strategy.add_argument("--version-len", default="2", help="Comma-separated versionLen values")
    p_swap_strategy.add_argument("--fees", default="0,0", help="Comma-separated fee list, last value must be zero")
    p_swap_strategy.add_argument("--fee-limit", type=int, default=500_000_000)
    p_swap_strategy.add_argument("--wait-for-receipt", action="store_true", help="Wait for on-chain receipt and fail fast when swap reverts")
    p_swap_strategy.add_argument("--receipt-timeout-s", type=int, default=90, help="Receipt polling timeout in seconds when --wait-for-receipt is enabled")
    p_swap_strategy.add_argument("--auto-amount-out-min", action="store_true", help="Quote expected output via calculation service/constant call and auto-set amountOutMin")
    p_swap_strategy.add_argument("--quote-slippage-bps", type=int, default=100, help="Slippage buffer (in bps) applied when --auto-amount-out-min is used")
    p_swap_strategy.add_argument("--intent-id", required=True, help="Unique intent id string (sha256-hashed to bytes32)")
    p_swap_strategy.add_argument("--plan-id", required=True, help="Unique plan id string (sha256-hashed to bytes32)")
    p_swap_strategy.add_argument("--strategy-module", default="", help="Optional strategy module override")
    p_swap_strategy.add_argument("--owner-address", default="", help="TRON owner/operator address")
    p_swap_strategy.add_argument("--private-key", default="", help="Signer private key hex")
    p_swap_strategy.add_argument("--signer-role", default=SIGNER_ROLE_EXECUTION, choices=["execution", "emergency", "analytics"], help="Signer wallet role")
    p_swap_strategy.add_argument("--permission-id", type=int, default=None, help="TRON permission id for Active Permission signing (0 uses owner/default)")
    p_swap_strategy.add_argument("--dry-run", action="store_true", help="Print normalized request without broadcasting")

    p_strategy = sub.add_parser("run-strategy-trx", help="Call strategy executeCallWithTRX with explicit target/calldata")
    p_strategy.add_argument("--config", default=None, help="Path to turnkey config JSON")
    p_strategy.add_argument("--intent-id", required=True, help="Unique intent id string (sha256-hashed to bytes32)")
    p_strategy.add_argument("--plan-id", required=True, help="Unique plan id string (sha256-hashed to bytes32)")
    p_strategy.add_argument("--amount-sun", type=int, required=True)
    p_strategy.add_argument("--target", required=True, help="Target contract address")
    p_strategy.add_argument("--call-data", required=True, help="Hex calldata (for sTRX deposit use 0xd0e30db0)")
    p_strategy.add_argument("--strategy-module", default="", help="Optional strategy module override")
    p_strategy.add_argument("--broadcast", action="store_true", help="Broadcast transaction to blockchain")
    p_strategy.add_argument("--wait-for-receipt", action="store_true", help="Poll for transaction receipt")
    p_strategy.add_argument("--owner-address", default="", help="TRON owner/operator address")
    p_strategy.add_argument("--private-key", default="", help="Signer private key hex")
    p_strategy.add_argument("--signer-role", default=SIGNER_ROLE_EXECUTION, choices=["execution", "emergency", "analytics"], help="Signer wallet role")
    p_strategy.add_argument("--permission-id", type=int, default=None, help="TRON permission id for Active Permission signing (0 uses owner/default)")

    p_run = sub.add_parser("run-jtrx", help="Run one jTRX decision+execution cycle")
    p_run.add_argument("--config", default=None, help="Path to turnkey config JSON")
    p_run.add_argument("--intent-id", required=True, help="Unique intent id")
    p_run.add_argument(
        "--action",
        required=True,
        choices=["mint", "redeem_underlying", "redeemUnderlying", "borrow", "repay_borrow", "repayBorrow", "redeem"],
    )
    p_run.add_argument("--amount-sun", type=int, required=True)
    p_run.add_argument("--strategy-amount-sun", type=int, default=None, help="Optional strategy transfer amount override")
    p_run.add_argument("--market", default="", help="Optional override market address (defaults to config jTRX market)")
    p_run.add_argument("--underlying-asset", default="", help="Underlying asset symbol/address for token market mint/repay (for example USDT or a token address)")
    p_run.add_argument("--requested-size-nav-pct", type=float, required=True)
    p_run.add_argument("--daily-cumulative-nav-pct", type=float, required=True)
    p_run.add_argument("--approved-by", default="", help="Comma-separated approver IDs")
    p_run.add_argument(
        "--approval-identities",
        default="",
        help="Comma-separated approval identity entries: approver_id|trc8004_subject|role",
    )
    p_run.add_argument("--degraded-data", action="store_true")
    p_run.add_argument("--emergency", action="store_true")
    p_run.add_argument("--ttl-s", type=int, default=300)
    p_run.add_argument("--broadcast", action="store_true", help="Broadcast transaction to blockchain")
    p_run.add_argument("--wait-for-receipt", action="store_true", help="Poll for transaction receipt")
    p_run.add_argument("--owner-address", default="", help="TRON owner/operator address")
    p_run.add_argument("--private-key", default="", help="Signer private key hex")
    p_run.add_argument("--signer-role", default=SIGNER_ROLE_EXECUTION, choices=["execution", "emergency", "analytics"], help="Signer wallet role")
    p_run.add_argument("--permission-id", type=int, default=None, help="TRON permission id for Active Permission signing (0 uses owner/default)")
    p_run.add_argument("--concise-output", action="store_true", help="Reduce logs to key metrics and execution summary")

    args = parser.parse_args()
    _configure_logging(args.verbose)

    if args.command == "health":
        return _run_health(args)
    if args.command == "monitor-account":
        return _run_monitor_account(args)
    if args.command == "check-borrow-ready":
        return _run_check_borrow_ready(args)
    if args.command == "setup-mcp-testnet":
        return _run_setup_mcp_testnet(args)
    if args.command == "search-reviewer-operator":
        return _run_search_reviewer_operator(args)
    if args.command == "package-openclaw-extension":
        return _run_package_openclaw_extension(args)
    if args.command == "run-autonomous-jtrx":
        try:
            return _run_autonomous_jtrx(args)
        except ValueError as exc:
            logging.getLogger("openclaw.turnkey").error(
                "Invalid run-autonomous-jtrx input: %s", exc
            )
            return 2
        except TronBroadcastError as exc:
            logging.getLogger("openclaw.turnkey").error(
                "Autonomous on-chain execution failed: %s", exc
            )
            return 3
    if args.command == "run-smart-router-swap":
        try:
            return _run_smart_router_swap(args)
        except ValueError as exc:
            logging.getLogger("openclaw.turnkey").error(
                "Smart Router execution failed: %s", exc
            )
            return 2
        except SmartRouterSwapError as exc:
            log = logging.getLogger("openclaw.turnkey")
            log.error("Smart Router execution failed: %s", exc)
            if getattr(exc, "diagnostics", None):
                log.error("Smart Router diagnostics:\n%s", json.dumps(exc.diagnostics, default=str, indent=2))
            return 2
    if args.command == "run-smart-router-strategy-swap":
        try:
            return _run_smart_router_strategy_swap(args)
        except ValueError as exc:
            logging.getLogger("openclaw.turnkey").error(
                "Smart Router strategy execution failed: %s", exc
            )
            return 2
        except SmartRouterSwapError as exc:
            log = logging.getLogger("openclaw.turnkey")
            log.error("Smart Router strategy execution failed: %s", exc)
            if getattr(exc, "diagnostics", None):
                log.error("Smart Router strategy diagnostics:\n%s", json.dumps(exc.diagnostics, default=str, indent=2))
            return 2
        except TronBroadcastError as exc:
            logging.getLogger("openclaw.turnkey").error(
                "On-chain execution failed: %s", exc
            )
            return 3
    if args.command == "run-jtrx":
        try:
            return _run_jtrx(args)
        except ValueError as exc:
            logging.getLogger("openclaw.turnkey").error(
                "Invalid run-jtrx input: %s", exc
            )
            return 2
        except TronBroadcastError as exc:
            logging.getLogger("openclaw.turnkey").error(
                "On-chain execution failed: %s", exc
            )
            return 3
    if args.command == "run-strategy-trx":
        try:
            return _run_strategy_trx(args)
        except TronBroadcastError as exc:
            logging.getLogger("openclaw.turnkey").error(
                "On-chain execution failed: %s", exc
            )
            return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
