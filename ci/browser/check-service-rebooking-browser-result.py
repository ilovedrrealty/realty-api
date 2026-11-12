"""Verify both the synthetic browser result and the independent database readback."""

import json
import re
import sys
from pathlib import Path


CHECKS = ['scoped_context_requires_owner_approved_work', 'spanish_mobile_blank_draft_uses_exact_beyond_first_page',
          'english_private_registry_repeat_draft_opens_without_list_reads', 'explicit_preview_and_one_new_request',
          'revoked_source_stops_repeat_and_existing_receipt_resolves', 'loopback_only_one_new_request_and_no_automatic_writes']
BROWSER = b'SERVICE_REBOOKING_BROWSER_RESULT '
DATABASE = b'SERVICE_REBOOKING_BROWSER_FIXTURE_RESULT '
FAILURE = b'BROWSER_API_FIXTURE_FAILURE '


def reports(path, prefix):
    assert path.is_file() and path.stat().st_size <= 131072
    lines = [line[len(prefix):] for line in path.read_bytes().splitlines() if line.startswith(prefix)]
    assert len(lines) <= 1 and all(len(line) <= 8192 for line in lines)
    return [json.loads(line) for line in lines]


def safe(value):
    if isinstance(value, dict):
        return len(value) <= 20 and all(re.fullmatch(r'[a-z_]+', key) and safe(item) for key, item in value.items())
    if isinstance(value, list):
        return len(value) <= 20 and all(safe(item) for item in value)
    if isinstance(value, str):
        return re.fullmatch(r'[A-Za-z0-9_ -]{1,100}', value) is not None
    return type(value) in (bool, int) or value is None


def emit(prefix, data):
    assert safe(data)
    print(prefix.decode() + json.dumps(data, sort_keys=True, separators=(',', ':')))


def main():
    mode = sys.argv[1]
    assert (mode, len(sys.argv)) in {('browser', 3), ('fixture', 4)}
    browser, = reports(Path(sys.argv[2]), BROWSER)
    if mode == 'browser':
        emit(BROWSER, browser)
    expected = {'status': 'PASS', 'scope': 'isolated_synthetic_service_rebooking', 'checks': CHECKS,
                'request_posts': 1, 'membership_revokes': 1, 'unexpected_browser_mutations': 0}
    assert set(browser) == set(expected) | {'blocked_external_requests'}
    assert all(type(browser[key]) is type(value) and browser[key] == value for key, value in expected.items())
    assert type(browser['blocked_external_requests']) is int and 0 <= browser['blocked_external_requests'] <= 100
    if mode == 'browser':
        return

    api = Path(sys.argv[3])
    failures = reports(api, FAILURE)
    if failures:
        failure, = failures
        assert set(failure) == {'stage', 'error_type', 'fixture_line'} and failure['stage'] == 'verify_rebooking'
        assert re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', failure['error_type'])
        assert failure['fixture_line'] is None or type(failure['fixture_line']) is int and 0 < failure['fixture_line'] <= 1000
        emit(FAILURE, failure)
        raise SystemExit(1)
    data, = reports(api, DATABASE)
    expected = {'status': 'PASS', 'requests_created': browser['request_posts'], 'new_recipient_count': 1,
                'original_listing_progress_entries': 1, 'original_private_progress_entries': 1,
                'original_award_unchanged': True, 'winner_membership_revoked': True,
                'provider_first_page_absent': True, 'listing_first_page_absent': True, 'external_transport_attempts': 0}
    assert data.keys() == expected.keys()
    assert all(type(data[key]) is type(value) and data[key] == value for key, value in expected.items())
    emit(DATABASE, data)


if __name__ == '__main__':
    main()
