import unittest

from openclaw_agent.turnkey.approvals import build_approval_artifacts, build_audit_bundle, parse_approval_identities


class ApprovalIdentityTests(unittest.TestCase):
    def test_parse_approval_identities(self):
        out = parse_approval_identities("alice|did:trc8004:alice|operator,bob|did:trc8004:bob")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].approver_id, "alice")
        self.assertEqual(out[0].trc8004_subject, "did:trc8004:alice")
        self.assertEqual(out[0].role, "operator")
        self.assertEqual(out[1].role, "reviewer")

    def test_parse_approval_identities_rejects_invalid_entries(self):
        with self.assertRaises(ValueError):
            parse_approval_identities("alice-only")

    def test_build_approval_artifacts_flags_missing_and_orphan(self):
        identities = parse_approval_identities("alice|did:trc8004:alice,bob|did:trc8004:bob")
        artifacts = build_approval_artifacts(["alice", "carol"], identities)
        self.assertEqual(artifacts["missing_identity_for_approved"], ["carol"])
        self.assertEqual(artifacts["orphan_identity_entries"], ["bob"])


class AuditBundleTests(unittest.TestCase):
    def test_build_audit_bundle_is_deterministic(self):
        payload = dict(
            network="tron-nile",
            chain_id="nile",
            owner="Towner",
            intent={"intent_id": "i"},
            decision={"allowed": True},
            plan={"execution_plan": {"plan_id": "p"}},
            receipt={"status": "simulated"},
            approval_artifacts={"approvers": ["alice"]},
        )
        one = build_audit_bundle(**payload)
        two = build_audit_bundle(**payload)
        self.assertEqual(one["audit_bundle_hash"], two["audit_bundle_hash"])
        self.assertTrue(one["audit_bundle_id"].startswith("audit_"))


if __name__ == "__main__":
    unittest.main()
