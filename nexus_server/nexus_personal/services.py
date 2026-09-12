"""Local installation only. No HTTP signup, IAM roles, plans or wallet records."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from apps.accounts.models import AccountProfile
from apps.tenancy.models import Project, Tenant
from .models import PersonalInstallation
from .configuration import require_personal_distribution


class PersonalSetupError(ValueError):
    pass


def provision_owner(*, email: str, password: str, display_name: str = ""):
    require_personal_distribution()
    email = email.strip().lower()
    display_name = display_name.strip()
    try:
        validate_email(email)
        if len(email) > 254 or len(display_name) > 255 or len(password) > 4096:
            raise ValidationError("Identity field is too long.")
        user = get_user_model()(username="nexus-owner", email=email, is_staff=False, is_superuser=False)
        validate_password(password, user=user)
    except ValidationError:
        # Validators can include user input. Never echo password/email in a CLI
        # exception or a process log; the caller can safely retry the prompt.
        raise PersonalSetupError("PERSONAL_IDENTITY_INVALID: Check email and password policy.") from None
    user.set_password(password)
    try:
        with transaction.atomic():
            if PersonalInstallation.objects.select_for_update().exists():
                raise PersonalSetupError("PERSONAL_ALREADY_CONFIGURED: The owner cannot be replaced by setup.")
            if get_user_model().objects.exists() or Tenant.objects.exists():
                raise PersonalSetupError("PERSONAL_DATABASE_NOT_EMPTY: Use a new personal database.")
            # Fixed username plus the database singleton constraint serialize
            # simultaneous first installs; a losing transaction rolls back all
            # rows. Never adopt an arbitrary pre-existing account or organization.
            user.save(force_insert=True)
            tenant = Tenant.objects.create(name="Personal", slug="personal")
            project = Project.objects.create(tenant=tenant, name="My Project", team=None)
            AccountProfile.objects.create(user=user, tenant_id=str(tenant.pk), project_id=str(project.pk),
                                          display_name=display_name)
            installation = PersonalInstallation.objects.create(slot=1, owner=user, tenant=tenant, project=project)
        return installation
    except IntegrityError:
        raise PersonalSetupError("PERSONAL_SETUP_CONFLICT: Reload installation state before retrying.") from None


def installation_context():
    require_personal_distribution()
    try:
        row = PersonalInstallation.objects.select_related("owner", "tenant", "project").get(slot=1)
    except PersonalInstallation.DoesNotExist:
        raise ImproperlyConfigured("Personal instance has not been initialized by its operator.") from None
    if not row.owner.is_active or row.tenant.status != "active" or row.project.status != "active" or row.project.tenant_id != row.tenant_id:
        raise ImproperlyConfigured("Personal installation context is unavailable or inconsistent.")
    return str(row.owner_id), str(row.tenant_id), str(row.project_id)
