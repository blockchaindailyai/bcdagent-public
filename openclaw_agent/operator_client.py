from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ENV_KEYS = [
    "OPENCLAW_RPC_URL",
    "OPENCLAW_SIGNER_RPC_URL",
    "OPENCLAW_USE_MCP_SERVER_TRON",
    "OPENCLAW_MCP_SERVER_TRON_URL",
    "OPENCLAW_SUBGRAPH_INDEXER_URL",
    "OPENCLAW_SUBGRAPH_INDEXER_LOCAL_PATH",
    "OPENCLAW_ENABLE_LOCAL_SIGNING_FALLBACK",
    "OPENCLAW_EXECUTION_PRIVATE_KEY",
    "OPENCLAW_EMERGENCY_PRIVATE_KEY",
    "OPENCLAW_ANALYTICS_PRIVATE_KEY",
    "OPENCLAW_EXECUTION_ADDRESS",
    "OPENCLAW_EMERGENCY_ADDRESS",
    "OPENCLAW_ANALYTICS_ADDRESS",
    "OPENCLAW_PERMISSION_ID",
]


@dataclass
class OperatorClientPaths:
    root: Path
    env_file: Path
    state_file: Path
    logs_dir: Path


class OpenClawOperatorClient:
    def __init__(self, root: Path | None = None) -> None:
        data_root = root or Path("openclaw_agent/data/operator_client")
        self.paths = OperatorClientPaths(
            root=data_root,
            env_file=data_root / "env.json",
            state_file=data_root / "state.json",
            logs_dir=data_root / "logs",
        )
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        self.paths.root.mkdir(parents=True, exist_ok=True)

    def initialize_nile_profile(self, force: bool = False) -> dict[str, str]:
        if self.paths.env_file.exists() and not force:
            return self.load_env_profile()

        profile = {
            "OPENCLAW_RPC_URL": "https://nile.trongrid.io",
            "OPENCLAW_SIGNER_RPC_URL": "https://nile.trongrid.io",
            "OPENCLAW_ENABLE_LOCAL_SIGNING_FALLBACK": "true",
            "OPENCLAW_USE_MCP_SERVER_TRON": "true",
            "OPENCLAW_MCP_SERVER_TRON_URL": "http://127.0.0.1:3000",
            "OPENCLAW_SUBGRAPH_INDEXER_LOCAL_PATH": "openclaw_agent/data/subgraph/events.jsonl",
            "OPENCLAW_PERMISSION_ID": "2",
        }
        self.save_env_profile(profile)
        return profile

    def load_env_profile(self) -> dict[str, str]:
        if not self.paths.env_file.exists():
            return {}
        return json.loads(self.paths.env_file.read_text(encoding="utf-8"))

    def save_env_profile(self, profile: dict[str, str]) -> None:
        self.paths.env_file.write_text(json.dumps(profile, indent=2, sort_keys=True), encoding="utf-8")

    def upsert_env(self, key: str, value: str) -> dict[str, str]:
        profile = self.load_env_profile()
        profile[key] = value
        self.save_env_profile(profile)
        return profile

    def build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.load_env_profile())
        return env

    def run_turnkey_json(self, args: list[str]) -> dict[str, Any]:
        cmd = [sys.executable, "-m", "openclaw_agent.turnkey", *args]
        completed = subprocess.run(cmd, capture_output=True, text=True, env=self.build_env(), check=False)
        stdout = completed.stdout.strip()
        if completed.returncode != 0:
            raise RuntimeError(f"turnkey command failed rc={completed.returncode} stderr={completed.stderr.strip()} stdout={stdout}")
        if not stdout:
            return {"status": "ok", "output": ""}
        return json.loads(stdout)

    def _load_state(self) -> dict[str, Any]:
        if not self.paths.state_file.exists():
            return {"autonomous_runs": {}}
        return json.loads(self.paths.state_file.read_text(encoding="utf-8"))

    def _save_state(self, payload: dict[str, Any]) -> None:
        self.paths.state_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _is_pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def start_autonomous(self, run_id: str, turnkey_args: list[str]) -> dict[str, Any]:
        state = self._load_state()
        run_map = state.setdefault("autonomous_runs", {})
        existing = run_map.get(run_id)
        if existing and self._is_pid_alive(int(existing.get("pid", 0))):
            raise RuntimeError(f"run_id={run_id} is already active with pid={existing['pid']}")

        log_file = self.paths.logs_dir / f"autonomous_{run_id}_{int(time.time())}.log"
        cmd = [sys.executable, "-m", "openclaw_agent.turnkey", "--verbose", "run-autonomous-jtrx", *turnkey_args]

        with log_file.open("a", encoding="utf-8") as handle:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=handle,
                stderr=subprocess.STDOUT,
                env=self.build_env(),
                start_new_session=True,
            )

        metadata = {
            "pid": proc.pid,
            "command": cmd,
            "log_file": str(log_file),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        run_map[run_id] = metadata
        self._save_state(state)
        return metadata

    def stop_autonomous(self, run_id: str) -> dict[str, Any]:
        state = self._load_state()
        run_map = state.setdefault("autonomous_runs", {})
        meta = run_map.get(run_id)
        if not meta:
            raise RuntimeError(f"run_id={run_id} was not found")

        pid = int(meta.get("pid", 0))
        if self._is_pid_alive(pid):
            os.killpg(pid, signal.SIGTERM)
            time.sleep(0.2)
            if self._is_pid_alive(pid):
                os.killpg(pid, signal.SIGKILL)

        meta["stopped_at"] = datetime.now(timezone.utc).isoformat()
        meta["active"] = False
        run_map[run_id] = meta
        self._save_state(state)
        return meta

    def autonomous_status(self) -> dict[str, Any]:
        state = self._load_state()
        run_map = state.setdefault("autonomous_runs", {})
        result: dict[str, Any] = {}
        for run_id, meta in run_map.items():
            pid = int(meta.get("pid", 0))
            result[run_id] = {
                **meta,
                "active": self._is_pid_alive(pid),
            }
        return result

    def read_recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        profile = self.load_env_profile()
        path = profile.get("OPENCLAW_SUBGRAPH_INDEXER_LOCAL_PATH", "openclaw_agent/data/subgraph/events.jsonl")
        event_path = Path(path)
        if not event_path.exists():
            return []
        lines = [line.strip() for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [json.loads(line) for line in lines[-limit:]]


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw operator-side local client for Nile/mainnet operations")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-nile", help="Create or reset a Nile-focused env profile")
    p_init.add_argument("--force", action="store_true")

    p_show = sub.add_parser("show-env", help="Show current operator environment profile")
    p_show.add_argument("--all", action="store_true", help="Show all keys, not just known OpenClaw defaults")

    p_set = sub.add_parser("set-env", help="Set or update one env setting in the operator profile")
    p_set.add_argument("--key", required=True)
    p_set.add_argument("--value", required=True)

    p_health = sub.add_parser("health", help="Run turnkey health")
    p_health.add_argument("--config", default="openclaw_agent/config/testnet_turnkey.json")

    p_monitor = sub.add_parser("monitor", help="Run turnkey monitor-account")
    p_monitor.add_argument("--config", default="openclaw_agent/config/testnet_turnkey.json")
    p_monitor.add_argument("--owner-address", default="")

    p_ready = sub.add_parser("borrow-ready", help="Run turnkey check-borrow-ready")
    p_ready.add_argument("--config", default="openclaw_agent/config/testnet_turnkey.json")

    p_search = sub.add_parser("review-search", help="Run reviewer/operator indexed search")
    p_search.add_argument("--config", default="openclaw_agent/config/testnet_turnkey.json")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--role", default="")
    p_search.add_argument("--limit", type=int, default=20)

    p_auto_start = sub.add_parser("auto-start", help="Start a background autonomous jTRX run")
    p_auto_start.add_argument("--run-id", required=True)
    p_auto_start.add_argument("--turnkey-args", nargs=argparse.REMAINDER, required=True, help="Arguments forwarded to run-autonomous-jtrx")

    p_auto_stop = sub.add_parser("auto-stop", help="Stop a background autonomous run")
    p_auto_stop.add_argument("--run-id", required=True)

    sub.add_parser("auto-status", help="Show all tracked autonomous run states")

    p_tail = sub.add_parser("tail-events", help="Read recent local indexed events for audits/reviews")
    p_tail.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    client = OpenClawOperatorClient()

    if args.command == "init-nile":
        _print({"status": "ok", "profile": client.initialize_nile_profile(force=args.force)})
        return 0

    if args.command == "show-env":
        profile = client.load_env_profile()
        if args.all:
            _print(profile)
        else:
            _print({k: profile.get(k, "") for k in DEFAULT_ENV_KEYS})
        return 0

    if args.command == "set-env":
        _print({"status": "ok", "profile": client.upsert_env(args.key, args.value)})
        return 0

    if args.command == "health":
        _print(client.run_turnkey_json(["health", "--config", args.config]))
        return 0

    if args.command == "monitor":
        cmd = ["monitor-account", "--config", args.config]
        if args.owner_address:
            cmd.extend(["--owner-address", args.owner_address])
        _print(client.run_turnkey_json(cmd))
        return 0

    if args.command == "borrow-ready":
        _print(client.run_turnkey_json(["check-borrow-ready", "--config", args.config]))
        return 0

    if args.command == "review-search":
        cmd = ["search-reviewer-operator", "--config", args.config, "--query", args.query, "--limit", str(args.limit)]
        if args.role:
            cmd.extend(["--role", args.role])
        _print(client.run_turnkey_json(cmd))
        return 0

    if args.command == "auto-start":
        _print({"status": "started", **client.start_autonomous(args.run_id, args.turnkey_args)})
        return 0

    if args.command == "auto-stop":
        _print({"status": "stopped", **client.stop_autonomous(args.run_id)})
        return 0

    if args.command == "auto-status":
        _print(client.autonomous_status())
        return 0

    if args.command == "tail-events":
        _print({"events": client.read_recent_events(limit=args.limit)})
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
