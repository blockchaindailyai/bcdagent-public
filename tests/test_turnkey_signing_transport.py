import os
import unittest
from unittest.mock import Mock, patch

from openclaw_agent.capabilities.policy_controls import ApprovalTier
from openclaw_agent.capabilities.execution_adapter import ExecutionPlanV2, ExecutionStep, PolicyDecisionV2
from openclaw_agent.turnkey.config import load_turnkey_config
from openclaw_agent.turnkey.jtrx_engine import JTRXExecutionPlan, JTRXExecutor
from openclaw_agent.turnkey.onchain import TronBroadcastError
from openclaw_agent.turnkey.signing import resolve_execution_signing_context, resolve_signing_context
from openclaw_agent.turnkey.transport import resolve_tron_transport_context


class SigningContextTests(unittest.TestCase):
    def test_signing_context_prefers_explicit_key(self):
        ctx = resolve_execution_signing_context("0xabc")
        self.assertEqual(ctx.private_key_hex, "0xabc")
        self.assertEqual(ctx.signer_label, "cli:manual-execution")

        self.assertEqual(ctx.signer_role, "execution")
        self.assertEqual(ctx.signer_source, "cli")
        self.assertTrue(ctx.signer_key_id.startswith("pkh:"))

    def test_signing_context_prefers_execution_env_over_operator(self):
        os.environ["OPENCLAW_EXECUTION_PRIVATE_KEY"] = "execution-key"
        os.environ["OPENCLAW_OPERATOR_PRIVATE_KEY"] = "operator-key"
        try:
            ctx = resolve_execution_signing_context("")
        finally:
            os.environ.pop("OPENCLAW_EXECUTION_PRIVATE_KEY", None)
            os.environ.pop("OPENCLAW_OPERATOR_PRIVATE_KEY", None)
        self.assertEqual(ctx.private_key_hex, "execution-key")
        self.assertEqual(ctx.signer_label, "env:execution")

        self.assertEqual(ctx.signer_role, "execution")
        self.assertEqual(ctx.signer_source, "env:OPENCLAW_EXECUTION_PRIVATE_KEY")
        self.assertTrue(ctx.signer_key_id.startswith("pkh:"))


    def test_emergency_signer_resolution_uses_emergency_env(self):
        os.environ["OPENCLAW_EMERGENCY_PRIVATE_KEY"] = "emergency-key"
        try:
            ctx = resolve_signing_context(signer_role="emergency")
        finally:
            os.environ.pop("OPENCLAW_EMERGENCY_PRIVATE_KEY", None)
        self.assertEqual(ctx.private_key_hex, "emergency-key")
        self.assertEqual(ctx.signer_role, "emergency")
        self.assertEqual(ctx.signer_source, "env:OPENCLAW_EMERGENCY_PRIVATE_KEY")


class ExecutionMetadataTests(unittest.TestCase):
    @staticmethod
    def _plan() -> JTRXExecutionPlan:
        return JTRXExecutionPlan(
            policy_decision=PolicyDecisionV2(
                intent_id="i",
                allowed=True,
                approval_tier=ApprovalTier.AUTO,
            ),
            execution_plan=ExecutionPlanV2(
                intent_id="i",
                plan_id="p",
                ordered_steps=[
                    ExecutionStep(name="justlend_strategy_call", params={"selector": "0x1249c58b"})
                ],
                max_slippage_bps=0,
                deadline_epoch_s=1,
                required_approvals=0,
            ),
        )

    def test_executor_includes_signer_metadata_and_observes(self):
        plan = self._plan()

        observer = Mock()
        receipt = JTRXExecutor(signer_label="env:execution", execution_observer=observer).execute(
            plan,
            approved_by=[],
            broadcast=False,
        )

        self.assertEqual(receipt.status, "simulated")
        self.assertEqual(receipt.metadata["signer"], "env:execution")
        self.assertEqual(receipt.metadata["transport"], "simulation")
        self.assertEqual(receipt.metadata["provenance_standard"], "bankofai-agent-wallet-v1")
        observer.assert_called_once()

    def test_executor_uses_broadcaster_transport_for_broadcast_error(self):
        class _FailingBroadcaster:
            transport = "mcp-server-tron"

            def broadcast_jtrx_plan(self, _plan, owner_address):
                raise TronBroadcastError("boom")

        receipt = JTRXExecutor(
            broadcaster=_FailingBroadcaster(),
            owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            signer_label="env:execution",
        ).execute(
            self._plan(),
            approved_by=[],
            broadcast=True,
            wait_for_receipt=False,
        )

        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.metadata["transport"], "mcp-server-tron")
        self.assertEqual(receipt.metadata["signer_role"], "execution")

    def test_executor_defaults_transport_when_broadcaster_lacks_transport(self):
        class _NoTransportBroadcaster:
            def broadcast_jtrx_plan(self, _plan, owner_address):
                raise TronBroadcastError("boom")

        receipt = JTRXExecutor(
            broadcaster=_NoTransportBroadcaster(),
            owner_address="TRwkyagc7W9vru91gKaC9r7cbZYo28ZSmY",
            signer_label="env:execution",
        ).execute(
            self._plan(),
            approved_by=[],
            broadcast=True,
            wait_for_receipt=False,
        )

        self.assertEqual(receipt.status, "failed")
        self.assertEqual(receipt.metadata["transport"], "tron-rpc")


class TransportContextTests(unittest.TestCase):
    @patch.dict(os.environ, {"OPENCLAW_USE_MCP_SERVER_TRON": "false", "OPENCLAW_MCP_SERVER_TRON_URL": ""}, clear=False)
    def test_transport_defaults_to_tron_rpc(self):
        cfg = load_turnkey_config()
        ctx = resolve_tron_transport_context(cfg)
        self.assertEqual(ctx.transport, "tron-rpc")
        self.assertTrue(ctx.api_base_url.startswith("https://"))

    def test_transport_uses_mcp_when_enabled_and_configured(self):
        cfg = load_turnkey_config()
        from dataclasses import replace

        cfg = replace(cfg, use_mcp_server_tron=True, mcp_server_tron_url="https://mcp.tron.local")
        ctx = resolve_tron_transport_context(cfg)
        self.assertEqual(ctx.transport, "mcp-server-tron")
        self.assertEqual(ctx.api_base_url, "https://mcp.tron.local")
        self.assertEqual(ctx.signer_base_url, "https://mcp.tron.local")


if __name__ == "__main__":
    unittest.main()
