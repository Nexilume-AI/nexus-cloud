"""Shared threshold/notification validation, with an explicit metric catalog."""
from decimal import Decimal, InvalidOperation
from rest_framework.exceptions import ValidationError


def validate_metric(*, metric, threshold, resource_type="", window_seconds=300, notification_channels=None, catalog, resource_types):
    name = metric
    if name not in catalog:
        raise ValidationError({"metric": "Select an available metric from the metric catalog."})
    definition = catalog[name]
    if not 1 <= int(window_seconds) <= 604800:
        raise ValidationError({"window_seconds": "Use a window between 1 second and 7 days."})
    if resource_type not in resource_types or (resource_type not in {"", "system"} and definition[1] != "window"):
        raise ValidationError({"resource_type": "This metric is not available for this resource type."})
    try:
        text = str(threshold).strip()
        value = Decimal(text.removesuffix("%"))
        if not value.is_finite() or abs(value) >= Decimal("1000000000000"):
            raise ValueError()
        if text.endswith("%") and definition[0] != "%":
            raise ValueError()
        if definition[0] == "%" and not 0 <= value <= 100:
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValidationError({"threshold": f"Use a finite {definition[0]} threshold (percentage: 0–100)."})
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError as DjangoValidationError
    channels = notification_channels or {}
    if not isinstance(channels, dict) or set(channels) - {"emails"}:
        raise ValidationError({"notification_channels": "Only email notification targets are supported."})
    targets = channels.get("emails", [])
    if not isinstance(targets, list) or len(targets) > 10:
        raise ValidationError({"notification_channels": "Specify at most ten email targets."})
    try:
        for target in targets:
            validate_email(target)
    except (DjangoValidationError, TypeError):
        raise ValidationError({"notification_channels": "Use valid email addresses."})
    return value, "%" if definition[0] == "%" else ""
