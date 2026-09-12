"""PostgreSQL-only configuration. There is deliberately no SQLite fallback."""
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured


def local_config_path(server_root):
    return Path(server_root).parent / ".local/nexus-cloud/postgres.json"


def transaction_options(environ):
    """Bound database work, not the duration of a streamed Agent invocation."""
    role = environ.get("NEXUS_PROCESS_ROLE", "web").strip().lower()
    defaults = {
        "web": (5000, 60000, 120000),
        "beat": (5000, 60000, 120000),
        "worker": (15000, 300000, 300000),
        "management": (60000, 1800000, 600000),
    }.get(role, (5000, 60000, 120000))
    names = ("lock_timeout", "statement_timeout", "idle_in_transaction_session_timeout")
    keys = ("NEXUS_DB_LOCK_TIMEOUT_MS", "NEXUS_DB_STATEMENT_TIMEOUT_MS", "NEXUS_DB_IDLE_TRANSACTION_TIMEOUT_MS")
    values = []
    for key, default in zip(keys, defaults):
        raw = str(environ.get(key, default))
        if not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) <= 2147483647:
            raise ImproperlyConfigured(f"{key} must be a positive PostgreSQL millisecond integer.")
        values.append(int(raw))
    if values[0] >= values[1]:
        raise ImproperlyConfigured("Database lock timeout must be less than statement timeout.")
    return " ".join(f"-c {name}={value}" for name, value in zip(names, values))


def database_config(environ, server_root):
    url = environ.get("DATABASE_URL", "")
    if url:
        try:
            parsed = urlparse(url)
            if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
                raise ValueError()
            config = {"ENGINE": "django.db.backends.postgresql", "NAME": unquote(parsed.path.lstrip("/")),
                "USER": unquote(parsed.username or ""), "PASSWORD": unquote(parsed.password or ""),
                "HOST": parsed.hostname, "PORT": parsed.port or 5432}
            options = parse_qs(parsed.query)
            allowed = {"sslmode", "sslrootcert", "sslcert", "sslkey", "application_name"}
            if set(options) - allowed:
                raise ValueError()
            config["OPTIONS"] = {key: values[-1] for key, values in options.items()}
        except (ValueError, TypeError):
            raise ImproperlyConfigured("DATABASE_URL must be a valid PostgreSQL URL; SQLite is not supported.") from None
    elif environ.get("POSTGRES_DB"):
        config = {"ENGINE": "django.db.backends.postgresql", "NAME": environ["POSTGRES_DB"],
            "USER": environ.get("POSTGRES_USER", "postgres"), "PASSWORD": environ.get("POSTGRES_PASSWORD", ""),
            "HOST": environ.get("POSTGRES_HOST", "localhost"), "PORT": environ.get("POSTGRES_PORT", "5432")}
    else:
        try:
            local = json.loads(local_config_path(server_root).read_text(encoding="utf-8"))
            config = {"ENGINE": "django.db.backends.postgresql", **{key: local[key] for key in ("NAME", "USER", "PASSWORD", "HOST", "PORT")}}
        except (OSError, ValueError, KeyError, TypeError):
            raise ImproperlyConfigured("PostgreSQL is required. Set DATABASE_URL/POSTGRES_DB or provision the local Docker PostgreSQL. SQLite fallback is disabled.") from None
    config.setdefault("OPTIONS", {}).setdefault("connect_timeout", 5)
    config["OPTIONS"].setdefault("application_name", "nexus-" + environ.get("NEXUS_PROCESS_ROLE", "local"))
    config["OPTIONS"]["options"] = transaction_options(environ)
    config["CONN_HEALTH_CHECKS"] = True
    return config
