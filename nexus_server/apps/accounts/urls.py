from __future__ import annotations

from django.urls import path

from .views import (
    GitHubOAuthCallbackView,
    GitHubOAuthStartView,
    GoogleOAuthCallbackView,
    GoogleOAuthStartView,
    LoginView,
    LogoutView,
    WhoAmIView,
)


urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("google/start/", GoogleOAuthStartView.as_view(), name="google-oauth-start"),
    path("google/callback/", GoogleOAuthCallbackView.as_view(), name="google-oauth-callback"),
    path("github/start/", GitHubOAuthStartView.as_view(), name="github-oauth-start"),
    path("github/callback/", GitHubOAuthCallbackView.as_view(), name="github-oauth-callback"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("whoami/", WhoAmIView.as_view(), name="whoami"),
]
