"""Personal host boundary: hosted Router credential issuance is unavailable."""
from rest_framework import exceptions

def available(row):
    return []

def issue(row, router_id, model):
    raise exceptions.PermissionDenied('Router delegation is not supported by this host. Use a caller-owned provider credential.')
