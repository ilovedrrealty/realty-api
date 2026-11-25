"""Pure constructor contracts: no application config, .env, DB or credentials."""
import ast
from pathlib import Path
import unittest

from utils.database_connection import engine_options


class DatabaseConnectionOptionsTests(unittest.TestCase):
    def test_psycopg2_connection_bounds_are_finite_and_not_statement_replay(self):
        expected = {"pool_pre_ping": True, "connect_args": {
            "connect_timeout": 10, "keepalives": 1, "keepalives_idle": 30,
            "keepalives_interval": 10, "keepalives_count": 3, "tcp_user_timeout": 60000}}
        for url in ("postgresql://synthetic:encoded%40password@localhost/owned",
                    "postgresql+psycopg2://localhost/owned", "postgresql:///owned?host=/tmp"):
            self.assertEqual(engine_options(url), expected)

    def test_sqlite_and_other_drivers_never_receive_libpq_arguments(self):
        for url in ("sqlite+pysqlite:///:memory:", "sqlite:///postgresql.db", "sqlite:///postgresql://nested",
                    "postgresql+asyncpg://localhost/owned", "postgresql+psycopg://localhost/owned",
                    "mysql+pymysql://localhost/owned"):
            self.assertEqual(engine_options(url), {"pool_pre_ping": True})

    def test_tls_auth_and_url_are_passed_verbatim_and_timeout_values_are_bounded(self):
        url = "postgresql+psycopg2://synthetic:p%40ss%3Aword@[::1]:55449/owned?sslmode=verify-full&sslrootcert=%2Fsynthetic%2Fca.pem&connect_timeout=0"
        options = engine_options(url)
        self.assertEqual(options["connect_args"]["connect_timeout"], 10)
        self.assertFalse({"sslmode", "sslrootcert", "user", "password", "host", "port", "dbname"}&set(options["connect_args"]))
        # Execute only the create_engine call from the production module, using
        # a constructor recorder and synthetic settings; never import config.
        module = ast.parse((Path(__file__).resolve().parents[1]/"database.py").read_text(encoding="utf-8"))
        constructor = next(node.value for node in module.body if isinstance(node, ast.Assign) and
            any(isinstance(target, ast.Name) and target.id=="engine" for target in node.targets))
        captured = []
        def create_engine(*args, **kwargs):
            captured.append((args, kwargs))
            return "synthetic-engine"
        settings = type("SyntheticSettings", (), {"database_url": url})()
        result = eval(compile(ast.Expression(constructor), "<owned-constructor-contract>", "eval"),
            {"create_engine": create_engine, "settings": settings, "engine_options": engine_options})
        self.assertEqual(result, "synthetic-engine")
        self.assertEqual(captured, [((url,), options)])

    def test_each_constructor_gets_a_fresh_option_dictionary(self):
        first = engine_options("postgresql://localhost/owned")
        first["connect_args"]["connect_timeout"] = 0
        first["pool_pre_ping"] = False
        second = engine_options("postgresql://localhost/owned")
        self.assertEqual(second["connect_args"]["connect_timeout"], 10)
        self.assertTrue(second["pool_pre_ping"])


if __name__ == "__main__":
    unittest.main()
