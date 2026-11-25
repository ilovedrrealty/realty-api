"""Print bounded source-owned facts only; failed native exits never become PASS."""
import json
from pathlib import Path
import re
import sys

mode, filename, native = sys.argv[1:]
assert mode in {'browser', 'api'} and re.fullmatch(r'[0-9]{1,3}', native)
path = Path(filename)
assert path.is_file() and path.stat().st_size <= 2 * 1024 * 1024
failed = False
presence, geometry, native_results, phone_traces = [], [], [], []
lines = path.read_bytes().splitlines()
for line in lines:
    if line.startswith(b'URGENT_SYNTHETIC_PHONE_TRACE '):
        assert mode == 'browser' and len(line) <= 16384 and len(phone_traces) < 2
        trace = json.loads(line.split(b' ', 1)[1])
        assert set(trace) == {'locale', 'rows'} and trace['locale'] in {'en', 'es'}
        assert trace['locale'] not in phone_traces and type(trace['rows']) is list and 2 <= len(trace['rows']) <= 64
        assert trace['rows'][0]['phase'] == 'initial' and trace['rows'][-1]['phase'] == 'final'
        for row in trace['rows']:
            assert set(row) == {'phase', 'digits', 'length', 'start', 'end', 'country', 'key', 'input_type', 'connected'}
            assert row['phase'] in {'initial', 'keydown', 'input', 'keyup', 'final'}
            assert type(row['digits']) is str and re.fullmatch(r'[0-9]{0,20}', row['digits'])
            assert all(type(row[key]) is int for key in ('length', 'start', 'end'))
            assert 0 <= row['length'] <= 40 and -1 <= row['start'] <= row['length'] and -1 <= row['end'] <= row['length']
            assert type(row['country']) is str and re.fullmatch(r'[a-z]{2}|unknown', row['country'])
            assert type(row['key']) is str and re.fullmatch(r'[0-9+]|End|none|other', row['key'])
            assert row['input_type'] in {'insertText', 'insertReplacementText', 'insertFromPaste', 'deleteContentBackward', 'none', 'other'}
            assert type(row['connected']) is bool
        phone_traces.append(trace['locale'])
        print('URGENT_SYNTHETIC_PHONE_TRACE ' + json.dumps(trace, sort_keys=True))
    if line.startswith(b'URGENT_NATIVE_RESULT '):
        assert mode == 'api' and len(line) <= 512 and not native_results
        counts = json.loads(line.split(b' ', 1)[1])
        assert set(counts) == {'tests', 'failures', 'errors', 'skipped', 'provider_attempts'}
        assert all(type(item) is int and 0 <= item <= 999 for item in counts.values())
        native_results.append(counts)
        if counts != {'tests': 8, 'failures': 0, 'errors': 0, 'skipped': 0, 'provider_attempts': 0}:
            failed = True
        print('URGENT_NATIVE_RESULT ' + json.dumps(counts, sort_keys=True))
    if line.startswith(b'URGENT_LOOPBACK_STATE '):
        assert mode == 'browser' and len(line) <= 256 and len(presence) < 4
        value = json.loads(line.split(b' ', 1)[1])
        assert set(value) == {'before', 'modeled', 'after', 'visible'} and all(type(item) is bool for item in value.values())
        assert value['modeled'] == (not value['before']) and value['after'] and value['visible']
        presence.append(value)
    if line.startswith(b'URGENT_BROWSER_GEOMETRY '):
        assert mode == 'browser' and len(line) <= 256 and len(geometry) < 8
        value = json.loads(line.split(b' ', 1)[1])
        assert set(value) == {'label', 'width', 'document'} and type(value['width']) is int and value['width'] in {320, 390}
        assert type(value['document']) is int and 0 < value['document'] <= value['width'] + 1
        assert value['label'] in {'en_buyer_assignment', 'es_buyer_assignment', 'en_signup', 'es_signup'}
        geometry.append(value)
    if line.startswith(b'URGENT_BROWSER_FAILURE '):
        assert len(line) <= 256 and re.fullmatch(rb'URGENT_BROWSER_FAILURE [A-Za-z][A-Za-z0-9_]{0,79} [A-Z][A-Za-z0-9_]{0,79}', line)
        print(line.decode('ascii')); failed = True
    if line.startswith(b'URGENT_FIXTURE_FAILURE '):
        assert mode == 'api' and len(line) <= 512
        value = json.loads(line.split(b' ', 1)[1])
        assert set(value) == {'stage', 'error_type', 'reason'} and all(isinstance(item, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,95}', item) for item in value.values())
        print('URGENT_FIXTURE_FAILURE ' + json.dumps(value, sort_keys=True)); failed = True
prefix = b'URGENT_BROWSER_RESULT ' if mode == 'browser' else b'URGENT_FIXTURE_RESULT '
rows = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
assert native == '0' and not failed and len(rows) == 1
value = rows[0]
if mode == 'browser':
    assert set(value) == {'status', 'geometry', 'locales', 'assignment_writes', 'signup_requests', 'page_errors', 'external_writes', 'blocked_external_requests'}
    assert value == {**value, 'status': 'PASS', 'geometry': 8, 'locales': ['en', 'es'], 'assignment_writes': 2,
        'signup_requests': 0, 'page_errors': 0, 'external_writes': 0}
    assert type(value['blocked_external_requests']) is int and 0 <= value['blocked_external_requests'] <= 999
    assert all(type(value[key]) is int for key in set(value) - {'status', 'locales'})
    assert phone_traces == ['en', 'es']
    assert len(presence) == 4 and len(geometry) == 8 and {(row['label'], row['width']) for row in geometry} == {
        (label, width) for label in ('en_buyer_assignment', 'es_buyer_assignment', 'en_signup', 'es_signup') for width in (320, 390)}
    for row in presence:
        print('URGENT_LOOPBACK_STATE ' + json.dumps(row, sort_keys=True))
    for row in geometry:
        print('URGENT_BROWSER_GEOMETRY ' + json.dumps(row, sort_keys=True))
else:
    expected = {'status': 'PASS', 'schema_tables': 99, 'native_tests': 8, 'browser_assignment_writes': 2,
        'audit_rows_added': 2, 'users_unchanged': 5, 'protected_owner_contacts_unchanged': 82,
        'contacts_unchanged': 85, 'sources_unchanged': 86, 'private_profiles_unchanged': 46,
        'accepted_jobs_unchanged': 9, 'cms_version_unchanged': 7, 'floorplans_unchanged': 2,
        'signup_writes': 0, 'unexpected_mutations': 0, 'external_provider_attempts': 0, 'private_files_removed': 1}
    assert value == expected
    assert all(type(value[key]) is int for key in set(value) - {'status'})
    assert native_results == [{'tests': 8, 'failures': 0, 'errors': 0, 'skipped': 0, 'provider_attempts': 0}]
print(prefix.decode() + json.dumps(value, sort_keys=True))
