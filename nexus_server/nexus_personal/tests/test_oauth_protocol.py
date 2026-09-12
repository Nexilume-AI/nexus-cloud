"""Shared protocol safety is retained without exposing public signup routes."""
import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.utils import timezone

from apps.accounts.github_oauth import (
    GITHUB_SESSION_KEY, GitHubOAuthError, begin_github_oauth,
    consume_github_oauth_session,
)
from tests.oauth_exchange_guards import GitHubOAuthExchangeGuards


@override_settings(
    NEXUS_GITHUB_OAUTH_CLIENT_ID="github-test-client",
    NEXUS_GITHUB_OAUTH_CLIENT_SECRET="github-test-secret",
    NEXUS_GITHUB_OAUTH_REDIRECT_URI="https://cloud.nexilume.test/api/v1/auth/github/callback/",
    NEXUS_GITHUB_OAUTH_STATE_TTL_SECONDS=60,
)
class PersonalOAuthProtocolTests(GitHubOAuthExchangeGuards, SimpleTestCase):
    def request(self):
        request = RequestFactory().get("/", secure=True)
        request.session = SessionStore()
        return request

    def test_pkce_state_and_verifier_are_one_use_and_not_in_redirect(self):
        request = self.request()
        url = begin_github_oauth(request=request, next_path="/agents")
        query = parse_qs(urlparse(url).query)
        stored = request.session[GITHUB_SESSION_KEY]
        expected = base64.urlsafe_b64encode(hashlib.sha256(stored["verifier"].encode("ascii")).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn(stored["verifier"], url)
        self.assertNotIn("github-test-secret", url)
        self.assertEqual(consume_github_oauth_session(request=request, state=query["state"][0]), stored)
        self.assertNotIn(GITHUB_SESSION_KEY, request.session)
        with self.assertRaises(GitHubOAuthError) as error:
            consume_github_oauth_session(request=request, state=query["state"][0])
        self.assertEqual(error.exception.code, "GITHUB_OAUTH_STATE_INVALID")

    def test_invalid_expired_or_future_state_is_consumed(self):
        cases = (("wrong_state", "GITHUB_OAUTH_STATE_INVALID"),
                 ("expired", "GITHUB_OAUTH_STATE_EXPIRED"),
                 ("future", "GITHUB_OAUTH_STATE_EXPIRED"),
                 ("short_verifier", "GITHUB_OAUTH_STATE_INVALID"))
        for case, expected in cases:
            with self.subTest(case=case):
                request = self.request()
                begin_github_oauth(request=request, next_path="/agents")
                stored = request.session[GITHUB_SESSION_KEY]
                state = stored["state"]
                if case == "wrong_state":
                    state = "different-state"
                elif case == "short_verifier":
                    stored["verifier"] = "short"
                else:
                    stored["created_at"] = int(timezone.now().timestamp()) + (120 if case == "future" else -120)
                with self.assertRaises(GitHubOAuthError) as error:
                    consume_github_oauth_session(request=request, state=state)
                self.assertEqual(error.exception.code, expected)
                self.assertNotIn(GITHUB_SESSION_KEY, request.session)
                self.assertNotIn(stored["verifier"], str(error.exception))

    def test_external_return_location_never_becomes_oauth_redirect_target(self):
        for next_path in ("https://untrusted.example/steal", "//untrusted.example/steal"):
            with self.subTest(next_path=next_path):
                request = self.request()
                begin_github_oauth(request=request, next_path=next_path)
                self.assertEqual(request.session[GITHUB_SESSION_KEY]["next"], "/")
