from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.common.models import SoftDeleteModel


class Tenant(SoftDeleteModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)

    class Meta:
        indexes = [
            models.Index(fields=["slug", "status"]),
        ]

    def __str__(self) -> str:
        return self.name


class Team(SoftDeleteModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="teams")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=255)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "parent", "status"]),
        ]

    def __str__(self) -> str:
        return self.name


class Project(SoftDeleteModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="projects")
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="projects",
    )
    name = models.CharField(max_length=255)
    instructions_markdown = models.TextField(blank=True)
    instructions_revision = models.PositiveIntegerField(default=0)
    instructions_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "team", "status"]),
        ]

    def __str__(self) -> str:
        return self.name


class Membership(SoftDeleteModel):
    # Membership expresses collaboration presence only. The legacy constants
    # remain temporarily available for data/import compatibility; authorization
    # is exclusively represented by IAM RoleBinding records.
    ROLE_OWNER = "owner"
    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = (
        (ROLE_MEMBER, "Member"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="memberships",
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tenant_memberships",
    )
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default=ROLE_MEMBER)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "team", "project", "user"],
                name="unique_active_membership_scope_user",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "user", "status"]),
            models.Index(fields=["tenant", "team", "status"]),
            models.Index(fields=["tenant", "project", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.tenant_id}:{self.role}"
