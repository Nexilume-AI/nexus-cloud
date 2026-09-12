from __future__ import annotations

from django.urls import path

from .views import AccountMeView, ChangePasswordView, PasswordResetView


urlpatterns = [
    path("me/", AccountMeView.as_view(), name="account-me"),
    path("change-password/", ChangePasswordView.as_view(), name="account-change-password"),
    path("password-reset/", PasswordResetView.as_view(), name="account-password-reset"),
]
