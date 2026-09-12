from __future__ import annotations

import hashlib
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import BaseModel, SoftDeleteModel


class AccountProfile(SoftDeleteModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="account_profile",
    )
    tenant_id = models.CharField(max_length=64, blank=True, db_index=True)
    project_id = models.CharField(max_length=64, blank=True, db_index=True)
    display_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=64, blank=True)
    company = models.CharField(max_length=255, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant_id", "user"]),
            models.Index(fields=["tenant_id", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.tenant_id or 'global'}"


class ExternalIdentity(BaseModel):
    PROVIDER_GOOGLE = "google"
    PROVIDER_GITHUB = "github"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="external_identities",
    )
    provider = models.CharField(max_length=32)
    # Provider subject is the stable identity key. Email is only a display snapshot.
    subject = models.CharField(max_length=255)
    email = models.EmailField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"], name="accounts_external_provider_subject_uniq"),
            models.UniqueConstraint(fields=["user", "provider"], name="accounts_external_user_provider_uniq"),
        ]
        indexes = [models.Index(fields=["provider", "email"])]


class PasswordResetToken(BaseModel):
    tenant_id = models.CharField(max_length=64, blank=True, db_index=True)
    project_id = models.CharField(max_length=64, blank=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token_hash = models.CharField(max_length=64, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant_id", "user", "created_at"]),
        ]

    @classmethod
    def create_for_user(cls, *, user, tenant_id: str = "", project_id: str = ""):
        raw_token = f"rst_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        reset_token = cls.objects.create(
            user=user,
            tenant_id=tenant_id,
            project_id=project_id,
            token_hash=token_hash,
            expires_at=timezone.now() + timezone.timedelta(hours=1),
        )
        return reset_token, raw_token

    def mark_used(self) -> None:
        self.used_at = timezone.now()
        self.save(update_fields=["used_at", "updated_at"])
