from importlib import import_module

import pytest

PHASE_TO_CAPABILITY = {
    "phase1": "policy_controls",
    "phase2": "execution_adapter",
    "phase3": "governance",
    "phase4": "config_management",
    "phase5": "verification",
    "phase6": "audit",
    "phase7": "rollout",
    "phase8": "operations",
}


def test_capability_exports_match_impl_exports() -> None:
    for capability in PHASE_TO_CAPABILITY.values():
        capability_module = import_module(f"openclaw_agent.capabilities.{capability}")
        impl_module = import_module(f"openclaw_agent.capabilities.{capability}_impl")

        assert set(capability_module.__all__) == set(impl_module.__all__)

        for name in capability_module.__all__:
            assert hasattr(capability_module, name)
            assert hasattr(impl_module, name)


def test_legacy_phase_modules_are_removed() -> None:
    for phase in PHASE_TO_CAPABILITY:
        with pytest.raises(ModuleNotFoundError):
            import_module(f"openclaw_agent.{phase}")
