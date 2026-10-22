"""Shared Personal browser capability checks."""
import hashlib
from django.utils import timezone
from rest_framework import exceptions
from .models import Agent

def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()

def live(row):
    if not row or row.closed or row.expires_at <= timezone.now() or row.run.agent.status != Agent.STATUS_ACTIVE:
        raise exceptions.NotFound('Interactive view is closed or expired. Open a new view through MCP.')
    # Personal credentials authenticate as their verified owner, never Enterprise Keys.
    from django.contrib.auth import get_user_model
    from nexus_personal.models import PersonalInvocationUsage
    if row.run.caller_principal_type != 'user' or not get_user_model().objects.filter(pk=row.run.caller_principal_id, is_active=True).exists():
        raise exceptions.NotFound('The viewer owner is no longer active.')
    credential_ids = PersonalInvocationUsage.objects.filter(run_id=row.run_id).exclude(agent_credential_id=None).values_list('agent_credential_id', flat=True)
    from nexus_personal.agent_credentials import _current
    from types import SimpleNamespace
    for credential_id in set(credential_ids):
        try:
            credential, user = _current(SimpleNamespace(META={}, headers={}), credential_id)
        except exceptions.APIException:
            raise exceptions.NotFound('The viewer credential is no longer active.') from None
        if str(user.pk) != row.run.caller_principal_id or str(row.run.agent_id) not in credential.agent_ids:
            raise exceptions.NotFound('The viewer credential no longer permits this Agent.')
    return row
