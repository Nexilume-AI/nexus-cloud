from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from apps.common.crypto import decrypt_secret, encrypt_secret


class SecretEncryptionKeyringTests(SimpleTestCase):
    def test_first_configured_key_encrypts_and_legacy_secret_key_still_decrypts(self):
        active_key = Fernet.generate_key().decode("ascii")
        legacy_ciphertext = encrypt_secret("legacy-value")

        with override_settings(NEXUS_SECRET_ENCRYPTION_KEYS=active_key):
            self.assertEqual(decrypt_secret(legacy_ciphertext), "legacy-value")
            rotated_ciphertext = encrypt_secret("rotated-value")
            self.assertEqual(Fernet(active_key.encode("ascii")).decrypt(rotated_ciphertext.encode("ascii")), b"rotated-value")

    def test_multiple_keys_allow_non_disruptive_rotation(self):
        old_key = Fernet.generate_key().decode("ascii")
        new_key = Fernet.generate_key().decode("ascii")
        with override_settings(NEXUS_SECRET_ENCRYPTION_KEYS=old_key):
            ciphertext = encrypt_secret("provider-secret")
        with override_settings(NEXUS_SECRET_ENCRYPTION_KEYS=f"{new_key},{old_key}"):
            self.assertEqual(decrypt_secret(ciphertext), "provider-secret")
