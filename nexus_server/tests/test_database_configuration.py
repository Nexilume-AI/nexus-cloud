import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.database import database_config, local_config_path


class PostgreSQLOnlyTests(SimpleTestCase):
    def test_missing_configuration_fails_closed(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ImproperlyConfigured, "SQLite fallback is disabled"):
                database_config({}, Path(directory) / "server")

    def test_sqlite_url_is_rejected_without_leaking_credentials(self):
        for url in ("sqlite:///db.sqlite3", "mysql://user:secret@localhost/db", "postgresql://user:secret@localhost:bad/db"):
            with self.assertRaises(ImproperlyConfigured) as error:
                database_config({"DATABASE_URL": url}, "/unused")
            self.assertNotIn("secret", str(error.exception))

    def test_url_has_priority_and_decodes_credentials(self):
        result = database_config({"DATABASE_URL": "postgresql://user:a%40b@127.0.0.1:55432/live?sslmode=require", "POSTGRES_DB": "ignored"}, "/unused")
        self.assertEqual(result["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(result["NAME"], "live")
        self.assertEqual(result["PASSWORD"], "a@b")
        self.assertEqual(result["OPTIONS"]["sslmode"], "require")

    def test_local_configuration_never_selects_sqlite(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "server"
            path = local_config_path(root)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"ENGINE": "django.db.backends.sqlite3", "NAME": "cloud", "USER": "cloud", "PASSWORD": "private", "HOST": "localhost", "PORT": 55432}))
            self.assertEqual(database_config({}, root)["ENGINE"], "django.db.backends.postgresql")

    def test_separate_postgres_environment(self):
        result = database_config({"POSTGRES_DB": "test", "POSTGRES_HOST": "postgres"}, "/unused")
        self.assertEqual(result["NAME"], "test")
        self.assertEqual(result["HOST"], "postgres")

    def test_role_specific_transaction_budgets(self):
        for role, expected in {
            "web": (5000, 60000, 120000),
            "worker": (15000, 300000, 300000),
            "beat": (5000, 60000, 120000),
            "management": (60000, 1800000, 600000),
        }.items():
            with self.subTest(role=role):
                options = database_config({"POSTGRES_DB": "test", "NEXUS_PROCESS_ROLE": role}, "/unused")["OPTIONS"]["options"]
                for name, value in zip(("lock_timeout", "statement_timeout", "idle_in_transaction_session_timeout"), expected):
                    self.assertIn(f"-c {name}={value}", options)
                self.assertNotIn("log_lock_waits", options)  # restricted runtime role cannot SET it

    def test_explicit_timeout_overrides_are_validated_without_echoing_input(self):
        result = database_config({"POSTGRES_DB": "test", "NEXUS_DB_LOCK_TIMEOUT_MS": "2500"}, "/unused")
        self.assertIn("lock_timeout=2500", result["OPTIONS"]["options"])
        for value in ("0", "-1", "secret -c role=postgres", "1.5", "2147483648"):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured) as error:
                database_config({"POSTGRES_DB": "test", "NEXUS_DB_LOCK_TIMEOUT_MS": value}, "/unused")
            self.assertNotIn(value, str(error.exception))

    def test_lock_budget_must_be_less_than_statement_budget(self):
        with self.assertRaises(ImproperlyConfigured):
            database_config({"POSTGRES_DB": "test", "NEXUS_DB_LOCK_TIMEOUT_MS": "60000", "NEXUS_DB_STATEMENT_TIMEOUT_MS": "10000"}, "/unused")

    def test_local_diagnostics_use_admin_reload_and_suppress_sql_payloads(self):
        from scripts import local_postgres
        db = MagicMock()
        db.__enter__.return_value = db
        db.execute.return_value.fetchone.return_value = "old"
        identity = json.dumps([{"Config": {"Labels": {local_postgres.LABEL: "cloud-postgres"}}}])
        with patch.object(local_postgres, "docker", return_value=identity), patch.object(local_postgres, "connect", return_value=db) as connect:
            local_postgres.configure_lock_diagnostics({})
        connect.assert_called_once_with({}, admin=True)
        statements = [call.args[0] if isinstance(call.args[0], str) else call.args[0].as_string() for call in db.execute.call_args_list]
        self.assertTrue(any('log_lock_waits' in value and "'on'" in value for value in statements))
        self.assertTrue(any('log_min_error_statement' in value and "'panic'" in value for value in statements))
        self.assertTrue(any('log_error_verbosity' in value and "'terse'" in value for value in statements))
        self.assertEqual(local_postgres.LOCK_DIAGNOSTICS["log_parameter_max_length_on_error"], "0")
        self.assertEqual(statements[-1], "SELECT pg_reload_conf()")
        self.assertTrue(db.autocommit)

    def test_local_diagnostics_refuse_unrecognized_container(self):
        from scripts import local_postgres
        with patch.object(local_postgres, "docker", return_value='[{"Config":{"Labels":{}}}]'), patch.object(local_postgres, "connect") as connect:
            with self.assertRaises(RuntimeError):
                local_postgres.configure_lock_diagnostics({})
        connect.assert_not_called()
