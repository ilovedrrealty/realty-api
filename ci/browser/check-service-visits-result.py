"""Accept bounded, synthetic proof emitted by the browser and database fixture."""

import json
import re
import sys
from pathlib import Path


BROWSER = b'SERVICE_VISITS_BROWSER_RESULT '
FIXTURE = b'SERVICE_VISITS_BROWSER_FIXTURE_RESULT '
FIXTURE_FAILURE = b'BROWSER_API_FIXTURE_FAILURE '
CHECKS = ['role_scoped_access', 'mutual_schedule_mobile_en_es', 'reschedule_retains_then_replaces',
          'cancel_and_rebook', 'completion_review_private', 'owner_approval_and_history',
          'loopback_only_and_no_automatic_writes']


def reports(path, prefix):
    assert path.is_file() and path.stat().st_size <= 131072
    lines = [line[len(prefix):] for line in path.read_bytes().splitlines() if line.startswith(prefix)]
    assert len(lines) <= 1 and all(len(line) <= 8192 for line in lines)
    return [json.loads(line) for line in lines]


def safe(value):
    if isinstance(value, dict):
        return len(value) <= 30 and all(re.fullmatch(r'[a-z_]+', key) and safe(item) for key, item in value.items())
    if isinstance(value, list):
        return len(value) <= 50 and all(safe(item) for item in value)
    if isinstance(value, str):
        return re.fullmatch(r'[a-zA-Z0-9_ -]{1,120}', value) is not None
    return value is None or type(value) in (bool, int)


def emit(prefix, value):
    assert safe(value)
    print(prefix.decode() + json.dumps(value, sort_keys=True, separators=(',', ':')))


def fixture_result(path):
    failures = reports(path, FIXTURE_FAILURE)
    if failures:
        failure, = failures
        assert set(failure) == {'stage', 'error_type', 'fixture_line'}
        assert failure['stage'] == 'verify_service_history'
        assert re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', failure['error_type'])
        assert failure['fixture_line'] is None or type(failure['fixture_line']) is int and 0 < failure['fixture_line'] <= 1000
        emit(FIXTURE_FAILURE, failure)
        raise SystemExit(1)

    data, = reports(path, FIXTURE)
    assert set(data) == {'status', 'work_status', 'version', 'actions', 'progress_entries',
                         'history_author_owner', 'clock_advanced', 'external_transport_attempts'}
    assert data['status'] == 'PASS' and data['work_status'] == 'completed'
    assert data['version'] == data['actions'] == 13
    assert data['progress_entries'] == 1 and data['history_author_owner'] is True
    assert data['clock_advanced'] is True and data['external_transport_attempts'] == 0
    return data


def verify_browser(data):
    expected = {
        'status': 'PASS', 'scope': 'isolated_synthetic_service_visits_browser', 'checks': CHECKS,
        'ui_work_posts': 13, 'unexpected_browser_mutations': 0, 'local_clock_advances': 1,
        'final_work_version': 13, 'exact_private_history_entries': 1,
    }
    assert data.keys() == expected.keys() | {'blocked_external_requests', 'online_override_pages'}
    assert all(type(data[key]) is type(value) and data[key] == value for key, value in expected.items())
    assert type(data['blocked_external_requests']) is int and 0 <= data['blocked_external_requests'] <= 100
    assert type(data['online_override_pages']) is int and 0 <= data['online_override_pages'] <= 5


def main():
    mode = sys.argv[1]
    assert (mode, len(sys.argv)) in {('browser', 3), ('fixture', 4)}
    browser, = reports(Path(sys.argv[2]), BROWSER)
    if mode == 'browser':
        emit(BROWSER, browser)
        verify_browser(browser)
        return

    verify_browser(browser)
    data = fixture_result(Path(sys.argv[3]))
    assert (data['version'], data['actions'], data['progress_entries']) == (
        browser['final_work_version'], browser['ui_work_posts'], browser['exact_private_history_entries']
    )
    emit(FIXTURE, data)


if __name__ == '__main__':
    main()
