from __future__ import annotations

import json
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from openclaw_agent.operator_client import DEFAULT_ENV_KEYS, OpenClawOperatorClient


class OperatorGuiApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("OpenClaw Operator GUI")
        self.root.geometry("900x680")
        self.client = OpenClawOperatorClient()

        self.config_path = tk.StringVar(value="openclaw_agent/config/testnet_turnkey.json")
        self.owner_address = tk.StringVar(value="")
        self.search_query = tk.StringVar(value="")
        self.search_role = tk.StringVar(value="")
        self.search_limit = tk.StringVar(value="20")
        self.events_limit = tk.StringVar(value="20")
        self.env_key = tk.StringVar(value="OPENCLAW_RPC_URL")
        self.env_value = tk.StringVar(value="https://nile.trongrid.io")

        self.env_vars: dict[str, tk.StringVar] = {key: tk.StringVar(value="") for key in DEFAULT_ENV_KEYS}

        self._build_layout()
        self.refresh_env()

    def _build_layout(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Nile/Testnet config path:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(frame, textvariable=self.config_path, width=70).grid(row=0, column=1, columnspan=6, sticky=tk.EW, padx=(8, 0))

        ttk.Label(frame, text="Owner address (optional):").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        ttk.Entry(frame, textvariable=self.owner_address, width=70).grid(row=1, column=1, columnspan=6, sticky=tk.EW, padx=(8, 0), pady=(8, 0))

        button_row = ttk.Frame(frame)
        button_row.grid(row=2, column=0, columnspan=7, sticky=tk.EW, pady=(10, 4))
        ttk.Button(button_row, text="Init Nile Profile", command=lambda: self._run_async(self.cmd_init_nile)).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(button_row, text="Health", command=lambda: self._run_async(self.cmd_health)).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(button_row, text="Borrow Ready", command=lambda: self._run_async(self.cmd_borrow_ready)).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(button_row, text="Monitor", command=lambda: self._run_async(self.cmd_monitor)).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(button_row, text="Tail Events", command=lambda: self._run_async(self.cmd_tail_events)).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(button_row, text="Refresh Env", command=self.refresh_env).pack(side=tk.LEFT)

        search_frame = ttk.LabelFrame(frame, text="Reviewer/Operator Search", padding=8)
        search_frame.grid(row=3, column=0, columnspan=7, sticky=tk.EW, pady=(8, 8))

        ttk.Label(search_frame, text="Query:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(search_frame, textvariable=self.search_query, width=40).grid(row=0, column=1, sticky=tk.EW, padx=(5, 8))
        ttk.Label(search_frame, text="Role:").grid(row=0, column=2, sticky=tk.W)
        ttk.Entry(search_frame, textvariable=self.search_role, width=20).grid(row=0, column=3, sticky=tk.EW, padx=(5, 8))
        ttk.Label(search_frame, text="Limit:").grid(row=0, column=4, sticky=tk.W)
        ttk.Entry(search_frame, textvariable=self.search_limit, width=6).grid(row=0, column=5, sticky=tk.W, padx=(5, 8))
        ttk.Button(search_frame, text="Run Search", command=lambda: self._run_async(self.cmd_search)).grid(row=0, column=6)

        env_frame = ttk.LabelFrame(frame, text="Environment", padding=8)
        env_frame.grid(row=4, column=0, columnspan=7, sticky=tk.EW)
        for idx, key in enumerate(DEFAULT_ENV_KEYS):
            ttk.Label(env_frame, text=key).grid(row=idx, column=0, sticky=tk.W)
            ttk.Entry(env_frame, textvariable=self.env_vars[key], width=88).grid(row=idx, column=1, sticky=tk.EW, padx=(8, 0))

        env_setter = ttk.Frame(env_frame)
        env_setter.grid(row=len(DEFAULT_ENV_KEYS), column=0, columnspan=2, sticky=tk.EW, pady=(10, 0))
        ttk.Entry(env_setter, textvariable=self.env_key, width=35).pack(side=tk.LEFT)
        ttk.Entry(env_setter, textvariable=self.env_value, width=45).pack(side=tk.LEFT, padx=(6, 6))
        ttk.Button(env_setter, text="Set Env", command=lambda: self._run_async(self.cmd_set_env)).pack(side=tk.LEFT)

        output_frame = ttk.LabelFrame(frame, text="Output", padding=8)
        output_frame.grid(row=5, column=0, columnspan=7, sticky=tk.NSEW, pady=(10, 0))
        self.output = scrolledtext.ScrolledText(output_frame, wrap=tk.WORD, height=15)
        self.output.pack(fill=tk.BOTH, expand=True)

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)
        search_frame.columnconfigure(1, weight=1)
        env_frame.columnconfigure(1, weight=1)

    def _run_async(self, fn) -> None:
        thread = threading.Thread(target=self._safe_call, args=(fn,), daemon=True)
        thread.start()

    def _safe_call(self, fn) -> None:
        try:
            payload = fn()
            self.root.after(0, lambda: self._render(payload))
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, lambda: messagebox.showerror("OpenClaw Operator GUI", str(exc)))

    def _render(self, payload) -> None:
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, json.dumps(payload, indent=2, default=str))

    def refresh_env(self) -> None:
        profile = self.client.load_env_profile()
        for key in DEFAULT_ENV_KEYS:
            self.env_vars[key].set(profile.get(key, ""))

    def cmd_init_nile(self):
        payload = {"status": "ok", "profile": self.client.initialize_nile_profile()}
        self.refresh_env()
        return payload

    def cmd_health(self):
        return self.client.run_turnkey_json(["health", "--config", self.config_path.get()])

    def cmd_borrow_ready(self):
        return self.client.run_turnkey_json(["check-borrow-ready", "--config", self.config_path.get()])

    def cmd_monitor(self):
        args = ["monitor-account", "--config", self.config_path.get()]
        if self.owner_address.get().strip():
            args.extend(["--owner-address", self.owner_address.get().strip()])
        return self.client.run_turnkey_json(args)

    def cmd_search(self):
        query = self.search_query.get().strip()
        if not query:
            raise ValueError("Search query is required")
        limit_value = int(self.search_limit.get().strip())
        args = [
            "search-reviewer-operator",
            "--config",
            self.config_path.get(),
            "--query",
            query,
            "--limit",
            str(limit_value),
        ]
        if self.search_role.get().strip():
            args.extend(["--role", self.search_role.get().strip()])
        return self.client.run_turnkey_json(args)

    def cmd_tail_events(self):
        limit_value = int(self.events_limit.get().strip())
        return {"events": self.client.read_recent_events(limit=limit_value)}

    def cmd_set_env(self):
        key = self.env_key.get().strip()
        value = self.env_value.get()
        if not key:
            raise ValueError("Environment key cannot be blank")
        payload = {"status": "ok", "profile": self.client.upsert_env(key, value)}
        self.refresh_env()
        return payload


def main() -> int:
    root = tk.Tk()
    OperatorGuiApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
