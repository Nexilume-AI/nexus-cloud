from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import AccountProfile


class WhoAmISerializer(serializers.Serializer):
    user_id = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    display_name = serializers.CharField(allow_blank=True)
    is_superuser = serializers.BooleanField()
    current_tenant = serializers.CharField(allow_blank=True)
    tenant_id = serializers.CharField(allow_blank=True)
    roles = serializers.ListField(child=serializers.CharField(), default=list)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False, write_only=True)


class LoginResponseSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    refresh_token = serializers.CharField()
    tenant_id = serializers.CharField(allow_blank=True)


class AccountProfileSerializer(serializers.Serializer):
    user_id = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True)
    username = serializers.CharField(read_only=True)
    display_name = serializers.CharField(allow_blank=True, required=False)
    phone = serializers.CharField(allow_blank=True, required=False)
    company = serializers.CharField(allow_blank=True, required=False)
    status = serializers.CharField(read_only=True)
    tenant_id = serializers.CharField(read_only=True)
    project_id = serializers.CharField(read_only=True)
    last_login_at = serializers.DateTimeField(read_only=True, allow_null=True)

    def to_representation(self, instance: AccountProfile) -> dict:
        user = instance.user
        return {
            "user_id": str(user.pk),
            "email": user.email,
            "username": user.username,
            "display_name": instance.display_name,
            "phone": instance.phone,
            "company": instance.company,
            "status": instance.status,
            "tenant_id": instance.tenant_id,
            "project_id": instance.project_id,
            "last_login_at": instance.last_login_at.isoformat() if instance.last_login_at else None,
        }

    def update(self, instance: AccountProfile, validated_data: dict) -> AccountProfile:
        for field in ("display_name", "phone", "company"):
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.save(update_fields=["display_name", "phone", "company", "updated_at"])
        return instance


class AccountUpdateSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=255, allow_blank=True, required=False)
    phone = serializers.CharField(max_length=64, allow_blank=True, required=False)
    company = serializers.CharField(max_length=255, allow_blank=True, required=False)

    def validate(self, attrs: dict) -> dict:
        if not attrs:
            raise serializers.ValidationError("At least one update field is required.")
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False, write_only=True)
    new_password = serializers.CharField(trim_whitespace=False, write_only=True)
    new_password_confirm = serializers.CharField(trim_whitespace=False, write_only=True)

    def validate(self, attrs: dict) -> dict:
        request = self.context["request"]
        user = request.user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": "Current password is incorrect."})
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password_confirm": "New passwords do not match."})
        if user.check_password(attrs["new_password"]):
            raise serializers.ValidationError({"new_password": "Choose a password you have not already used here."})
        try:
            validate_password(attrs["new_password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)}) from exc
        return attrs


class ChangePasswordResultSerializer(serializers.Serializer):
    status = serializers.CharField()


class PasswordResetSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        user_model = get_user_model()
        if not user_model.objects.filter(email__iexact=value, is_active=True).exists():
            raise serializers.ValidationError("No active account exists for this email.")
        return value
