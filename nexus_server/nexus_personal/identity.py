from apps.accounts.personal_identity import PersonalIdentityBackend
from .services import installation_context


class DatabasePersonalIdentityBackend(PersonalIdentityBackend):
    def owner_context(self):
        # Read the authoritative row on each authentication; do not cache owner
        # identity or let operator configuration/header changes select a user.
        return installation_context()
