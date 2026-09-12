from __future__ import annotations

from rest_framework.permissions import IsAuthenticated


class IsAuthenticatedJobUser(IsAuthenticated):
    pass
