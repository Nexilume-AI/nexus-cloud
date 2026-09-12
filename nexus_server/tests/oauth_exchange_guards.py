"""Shared identity exchange checks, independent of signup and role provisioning."""
from unittest.mock import patch

from apps.accounts.github_oauth import GitHubOAuthError, exchange_github_identity


class GitHubOAuthExchangeGuards:
    @patch("apps.accounts.github_oauth._request_json")
    def test_exchange_uses_verified_primary_email_and_does_not_return_token(self, request_json) -> None:
        request_json.side_effect = [
            {"access_token": "temporary-token", "token_type": "bearer"},
            {"id": 505, "login": "octocat", "name": "Octo Cat"},
            [{"email": "octo@example.com", "primary": True, "verified": True}],
        ]
        identity = exchange_github_identity(code="code", verifier="v" * 64)
        self.assertEqual(identity, {"subject": "505", "email": "octo@example.com", "display_name": "Octo Cat"})
        self.assertNotIn("token", identity)

    @patch("apps.accounts.github_oauth._request_json")
    def test_exchange_rejects_missing_verified_primary_email(self, request_json) -> None:
        request_json.side_effect = [
            {"access_token": "temporary-token"},
            {"id": 606, "login": "octocat"},
            [{"email": "octo@example.com", "primary": True, "verified": False}],
        ]
        with self.assertRaisesRegex(GitHubOAuthError, "verified primary email"):
            exchange_github_identity(code="code", verifier="v" * 64)
