from openclaw_agent.capabilities import governance, policy_controls


def test_capabilities_import_surface_matches_legacy_phase_modules():
    assert policy_controls.ApprovalTier.AUTO.value == "auto"
    assert governance.AUTO_MAX_ACTION_NAV_PCT == 1.5
