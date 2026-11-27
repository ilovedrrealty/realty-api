"""Pure owned MockTransport proof; never connect to a database or model."""
import ast
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from hashlib import sha256
from importlib.metadata import version
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
import logging
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parent
PLAN = json.loads((TOOLS / "PLAN.json").read_bytes())
STAGE = "source"
NETWORK = []
HTTP = []
MOCK = []


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def emit(kind, value):
    print("GATEWAY_UNIT_" + kind + " " + json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def refuse_network(*_args, **_kwargs):
    NETWORK.append(1)
    raise Failure("real_network_forbidden_required")


def refuse_http(*_args, **_kwargs):
    HTTP.append(1)
    raise Failure("real_http_forbidden_required")


def run():
    global STAGE
    logging.disable(logging.CRITICAL)
    app = Path("/application")
    require(sys.version_info[:2] == (3, 11) and version("httpx") == "0.27.0", "pinned_runtime_required")
    require(os.statvfs(app).f_flag & os.ST_RDONLY and os.statvfs(TOOLS).f_flag & os.ST_RDONLY,
            "readonly_sources_required")
    for name, pin in PLAN["source_files"].items():
        path = app / name
        require(path.is_file() and not path.is_symlink() and sha256(path.read_bytes()).hexdigest() == pin["sha256"],
                "exact_source_bytes_required")
    source = app / PLAN["suite"]["file"]
    classes = [n for n in ast.parse(source.read_bytes()).body if isinstance(n, ast.ClassDef)
               and n.name == PLAN["suite"]["class"]]
    require(len(classes) == 1 and [n.name for n in classes[0].body if isinstance(n, ast.AsyncFunctionDef)
            and n.name.startswith("test_")] == PLAN["suite"]["methods"], "exact_twelve_inventory_required")
    captured = StringIO()
    STAGE = "suite"
    with ExitStack() as stack, redirect_stdout(captured), redirect_stderr(captured):
        import httpx
        original_sync = httpx.MockTransport.handle_request
        original_async = httpx.MockTransport.handle_async_request

        def observed_sync(self, request):
            MOCK.append("sync")
            return original_sync(self, request)

        async def observed_async(self, request):
            MOCK.append("async")
            return await original_async(self, request)

        for target, name, replacement in (
            (socket, "create_connection", refuse_network), (socket, "getaddrinfo", refuse_network),
            (socket.socket, "connect", refuse_network), (socket.socket, "connect_ex", refuse_network),
            (httpx.HTTPTransport, "handle_request", refuse_http),
            (httpx.AsyncHTTPTransport, "handle_async_request", refuse_http),
            (httpx.MockTransport, "handle_request", observed_sync),
            (httpx.MockTransport, "handle_async_request", observed_async),
        ):
            stack.enter_context(patch.object(target, name, replacement))
        sys.path.insert(0, str(app))
        spec = spec_from_file_location("owned_gateway_unit_cases", source)
        module = module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        case = getattr(module, PLAN["suite"]["class"])
        methods = PLAN["suite"]["methods"]
        require(len(methods) == len(set(methods)) == 12 and getattr(case, "__unittest_skip__", False) is False
                and all(getattr(getattr(case, name), "__unittest_skip__", False) is False for name in methods),
                "no_skipped_cases_required")
        result = unittest.TestResult()
        unittest.TestSuite(case(name) for name in methods).run(result)
    emit("COUNTS", {"tests": result.testsRun, "failures": len(result.failures),
                    "errors": len(result.errors), "skipped": len(result.skipped)})
    for name in sorted({getattr(case, "_testMethodName", "fixture_setup_or_cleanup")
                        for case, _ in result.failures + result.errors}):
        emit("FAILED_METHOD", {"method": name if name in methods else "fixture_setup_or_cleanup"})
    require(result.testsRun == 12 and result.wasSuccessful() and not result.skipped and not result.expectedFailures
            and not result.unexpectedSuccesses and len(captured.getvalue().encode()) <= 2097152,
            "all_twelve_cases_required")
    require(not NETWORK and not HTTP and 1 <= len(MOCK) <= 128 and MOCK.count("async") > 0,
            "only_owned_mock_calls_required")
    require(all(name not in sys.modules for name in ("config", "database", "main", "models", "routes.listings")),
            "no_application_imports_required")
    STAGE = "preservation"
    for name, pin in PLAN["source_files"].items():
        require(sha256((app / name).read_bytes()).hexdigest() == pin["sha256"], "source_preservation_required")
    emit("RESULT", {"status": "PASS", "tests": 12, "exact_source": True, "httpx": "0.27.0",
        "mock_sync_calls": MOCK.count("sync"), "mock_async_calls": MOCK.count("async"),
        "real_network_attempts": 0, "real_http_attempts": 0, "actual_model_calls": 0,
        "selected_module_no_app_imports": True, "database_used": False, "model_access_claim": False,
        "cost_entitlement_claim": False, "source_unchanged": True})


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        allowed = {"Failure", "AssertionError", "RuntimeError", "ValueError", "TypeError", "KeyError", "ImportError"}
        emit("FAILURE", {"stage": STAGE, "error_type": type(error).__name__ if type(error).__name__ in allowed else "other",
             "reason": str(error) if isinstance(error, Failure) else "unexpected_exception"})
        raise SystemExit(1) from None
