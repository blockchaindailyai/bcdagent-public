import unittest

from openclaw_agent.identity_rails import StaticTrc8004Provider, Trc8004IdentityClient
from openclaw_agent.turnkey.approvals import build_approval_artifacts, parse_approval_identities


class Trc8004SdkTests(unittest.TestCase):
    def test_subject_validation(self):
        self.assertTrue(Trc8004IdentityClient.is_valid_subject("did:trc8004:policy-engine-1"))
        self.assertFalse(Trc8004IdentityClient.is_valid_subject("did:other:abc"))

    def test_resolve_identity(self):
        provider = StaticTrc8004Provider(
            {
                "alice": {
                    "subject": "did:trc8004:alice",
                    "role": "reviewer",
                    "issuedAt": 1710000000,
                    "expiresAt": 0,
                    "revoked": False,
                }
            }
        )
        client = Trc8004IdentityClient(provider)
        record = client.resolve("alice")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertTrue(record.is_active)
        self.assertEqual(record.subject, "did:trc8004:alice")

    def test_approval_artifacts_include_onchain_status(self):
        identities = parse_approval_identities("alice|did:trc8004:alice|operator")
        provider = StaticTrc8004Provider(
            {
                "alice": {
                    "subject": "did:trc8004:alice",
                    "role": "operator",
                    "issuedAt": 1710000000,
                    "expiresAt": 0,
                    "revoked": False,
                }
            }
        )
        artifacts = build_approval_artifacts(["alice"], identities, identity_client=Trc8004IdentityClient(provider))
        self.assertTrue(artifacts["evidence"][0]["subject_format_valid"])
        self.assertTrue(artifacts["evidence"][0]["onchain_active"])


if __name__ == "__main__":
    unittest.main()
