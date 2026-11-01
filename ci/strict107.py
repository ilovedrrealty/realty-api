"""Manual isolated Linux CI strict107 candidate. No deploy or migration acceptance.

Parent owns one child and three nonce schemas. Docker workflow owns the NEW PG17
container/internal network. No application main/lifespan or real provider runs.
This source candidate has not been imported, parsed or executed by its author.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
import re
import selectors
import socket
import subprocess
import sys
import sysconfig
import time
import types
import unittest
from uuid import UUID, uuid4

REPO = Path('/application')
ARTIFACTS = Path('/artifacts')
SCRATCH = Path('/scratch')
MANIFEST = REPO/'ci/strict107-source.json'
SELF = REPO/'ci/strict107.py'
URL = 'postgresql+psycopg2://postgres@127.0.0.1:55449/ildr_platform_test_20261025?connect_timeout=5'
TARGETS = (
    ('tests/test_property_network.py', (('PropertyNetworkRoutes', 18), ('PrivateImageContracts', 4)), 'network_test_'),
    ('tests/test_customer_delivery.py', (('CustomerDeliveryTests', 18),), 'customer_delivery_test_'),
    ('tests/test_lead_delivery.py', (('LeadDeliveryTests', 52),), 'lead_delivery_test_'),
    ('tests/test_signup_notifications.py', (('SignupNotificationTests', 15),), None),
)
OPERATIONAL_PATHS = ('ci/strict107.py', 'ci/strict107-source.json',
                     'ci/strict107.Dockerfile', '.github/workflows/strict107.yml')


def need(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while part := stream.read(65536):
            value.update(part)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def bootstrap_sites():
    need(sys.platform == 'linux' and sys.version_info[:2] == (3, 11)
         and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
         'LINUX_PYTHON311_I_S_B_REQUIRED')
    for kind in ('purelib', 'platlib'):
        path = Path(sysconfig.get_paths()[kind]).resolve()
        need(path.is_relative_to(Path('/usr/local')) and path.name == 'site-packages', 'FIXED_IMAGE_SITE_PACKAGES_REQUIRED')
        if str(path) not in sys.path:
            sys.path.append(str(path))


def load_manifest():
    need(MANIFEST.is_file() and not MANIFEST.is_symlink() and MANIFEST.stat().st_size < 262144, 'SOURCE_MANIFEST_REQUIRED')
    raw = MANIFEST.read_bytes()
    value = json.loads(raw)
    need(value.get('format') == 1 and value.get('expected_tests') == 107
         and value.get('bootstrap_and_full_migration_chain_proven') is False
         and len(value.get('files', {})) == 186
         and value.get('operational_paths') == list(OPERATIONAL_PATHS)
         and value.get('expected_classes') == {name: count for _, classes, _ in TARGETS for name, count in classes},
         'EXACT_186_SOURCE_107_SCOPE_REQUIRED')
    return value, sha(raw)


def working_source(manifest):
    for name, item in manifest['files'].items():
        path = REPO/name
        need(not Path(name).is_absolute() and '..' not in Path(name).parts and path.is_file()
             and not path.is_symlink() and path.resolve().is_relative_to(REPO), 'REGULAR_IN_REPOSITORY_SOURCE_REQUIRED')
        need(path.stat().st_size == item['bytes'] and file_sha(path) == item['sha256'], 'EXACT_LF_WORKING_SOURCE_REQUIRED')


def git_source(manifest, commit):
    def git(*arguments):
        return subprocess.check_output(['git', '--no-optional-locks', '-c', 'safe.directory=/application',
            '-C', str(REPO), *arguments], stderr=subprocess.PIPE, timeout=20,
            env={'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'), 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'})
    need(re.fullmatch(r'[0-9a-f]{40}', commit or '') and git('rev-parse', 'HEAD').decode().strip() == commit,
         'EXPLICIT_EXACT_CANDIDATE_COMMIT_REQUIRED')
    entries = {}
    for record in git('ls-tree', '-r', '-z', 'HEAD').split(b'\0'):
        if not record:
            continue
        meta, name = record.split(b'\t', 1)
        mode, kind, oid = meta.decode('ascii').split()
        need(mode in ('100644', '100755') and kind == 'blob', 'REGULAR_GIT_BLOBS_ONLY')
        entries[name.decode('utf-8')] = oid
    need(set(entries) == set(manifest['files']) | set(manifest['operational_paths']), 'ONLY_REVIEWED_SOURCE_AND_FOUR_CI_FILES')
    for name, item in manifest['files'].items():
        blob = git('show', commit+':'+name)
        need(entries[name] == item['git_blob_oid'] and len(blob) == item['bytes'] and sha(blob) == item['sha256'],
             'ACTUAL_GIT_BLOB_LF_IDENTITY_REQUIRED')
    need(not git('status', '--porcelain').strip(), 'CLEAN_EXACT_CANDIDATE_CHECKOUT_REQUIRED')
    working_source(manifest)
    return {name: entries[name] for name in manifest['operational_paths']}


def linux_inventory(manifest_pin, commit, operational):
    import PIL
    from PIL import _imaging, features
    need(PIL.__version__ == '10.4.0' and features.check('webp'), 'EXACT_PILLOW104_WEBP_REQUIRED')
    packages = []
    for distribution in importlib.metadata.distributions():
        records = []
        need(distribution.files is not None, 'INSTALLED_DISTRIBUTION_FILE_INVENTORY_REQUIRED')
        for member in distribution.files:
            path = Path(distribution.locate_file(member))
            need(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(Path('/usr/local')),
                 'REGULAR_IMAGE_DEPENDENCY_FILE_REQUIRED')
            records.append((str(member), path.stat().st_size, file_sha(path)))
        packages.append({'name': distribution.metadata['Name'], 'version': distribution.version,
                         'file_count': len(records), 'files_sha256': sha(canonical(sorted(records)))})
    packages.sort(key=lambda row: row['name'].lower())
    installed = {re.sub(r'[-_.]+', '-', row['name']).lower(): row['version'] for row in packages}
    need(len(installed) == len(packages), 'UNIQUE_INSTALLED_DISTRIBUTIONS_REQUIRED')
    for line in (REPO/'requirements.txt').read_text().splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?==([^\s]+)', line.strip())
        need(match is not None and installed.get(re.sub(r'[-_.]+', '-', match[1]).lower()) == match[2], 'PRODUCTION_REQUIREMENT_VERSION_MISMATCH')
    return {'scope': 'LINUX_RUNTIME_OBSERVATION_NOT_RELEASE_ACCEPTANCE', 'source_commit': commit,
        'source_manifest_sha256': manifest_pin, 'operational_git_blobs': operational,
        'python_version': platform.python_version(), 'python_executable_sha256': file_sha(Path(sys.executable).resolve()),
        'git_executable_sha256': file_sha(Path('/usr/bin/git')),
        'platform': platform.platform(), 'machine': platform.machine(), 'packages': packages,
        'pillow_version': PIL.__version__, 'pillow_imaging_sha256': file_sha(Path(_imaging.__file__)),
        'webp_available': True, 'webp_version': features.version_module('webp'),
        'requirements_sha256': file_sha(REPO/'requirements.txt'), 'windows_native_pins_reused': False,
        'bootstrap_and_full_migration_chain_proven': False}


def available_mib():
    rows = Path('/proc/meminfo').read_text().splitlines()
    values = [int(row.split()[1]) / 1024 for row in rows if row.startswith('MemAvailable:')]
    need(len(values) == 1, 'LINUX_AVAILABLE_MEMORY_REQUIRED')
    # Respect the container limit as well as the Linux host's available RAM.
    maximum, current = Path('/sys/fs/cgroup/memory.max'), Path('/sys/fs/cgroup/memory.current')
    need(maximum.is_file() and current.is_file(), 'CGROUP_V2_MEMORY_ACCOUNTING_REQUIRED')
    limit = maximum.read_text().strip()
    return values[0] if limit == 'max' else min(values[0], (int(limit)-int(current.read_text()))/1048576)


def local_connection():
    import psycopg2
    connection = psycopg2.connect(host='127.0.0.1', port=55449, dbname='ildr_platform_test_20261025',
                                  user='postgres', connect_timeout=5)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(),current_user,host(inet_server_addr()),inet_server_port(),current_setting(\'server_version_num\')')
            identity = cursor.fetchone()
        need(identity[:4] == ('ildr_platform_test_20261025', 'postgres', '127.0.0.1', 55449)
             and 170000 <= int(identity[4]) < 180000, 'OWNED_NAMESPACE_EXACT_PG17_IDENTITY_REQUIRED')
        return connection, int(identity[4])
    except BaseException:
        connection.close()
        raise


def install_child_guard(denied):
    original_pair = socket.socketpair
    def local_pair(*args, **kwargs):
        need(not args and not kwargs, 'ONLY_DEFAULT_LINUX_SELF_PIPE_ALLOWED')
        # Linux native default AF_UNIX socketpair needs no TCP bind/connect.
        return original_pair()
    def audit(event, values):
        reject = event in ('subprocess.Popen', 'os.system', 'os.posix_spawn', 'socket.bind', 'socket.sendto', 'socket.sendmsg')
        if event == 'socket.connect':
            address = values[1]
            reject = not (isinstance(address, tuple) and len(address) >= 2 and address[0] == '127.0.0.1' and address[1] == 55449)
        elif event in ('socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'):
            reject = values[0] not in ('127.0.0.1', 'localhost', None)
        if reject:
            denied.append(event)
            raise PermissionError('Non-test network or process denied')
    sys.addaudithook(audit)
    socket.socketpair = local_pair


def child(args, manifest, manifest_pin):
    nonce = UUID(args.nonce)
    need(nonce.version == 4 and nonce.hex == args.nonce and Path.cwd().parent == SCRATCH
         and Path.cwd().name == 'strict107-'+nonce.hex, 'EXACT_NEW_CHILD_CWD_AND_NONCE_REQUIRED')
    need(args.manifest_sha256 == manifest_pin and not Path('.env').exists() and not (REPO/'.env').exists(), 'NO_ENVIRONMENT_FILE_REQUIRED')
    keep = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TZ') if key in os.environ}
    os.environ.clear()
    os.environ.update(keep, DATABASE_URL='sqlite+pysqlite:///:memory:', SEARCH_FILTER_TEST_DATABASE_URL=URL,
        JWT_SECRET='strict107-synthetic-signing-key-only', GHL_ENABLED='false', GHL_API_KEY='',
        GHL_LOCATION_ID='', GHL_WEBHOOK_SECRET='', LEAD_DELIVERY_ENABLED='false', CUSTOMER_MESSAGES_ENABLED='false',
        RESEND_API_KEY='', NOTIFY_EMAIL='', SUPABASE_URL='', SUPABASE_SERVICE_KEY='',
        PAYPAL_CLIENT_ID='', PAYPAL_CLIENT_SECRET='', PAYPAL_WEBHOOK_ID='', ENVIRONMENT='development', PYTHONDONTWRITEBYTECODE='1')
    working_source(manifest)
    denied, modules = [], {}
    install_child_guard(denied)
    sys.path.insert(0, str(REPO))
    expected = {name: prefix+nonce.hex for name, _, prefix in TARGETS if prefix}
    report = {'status': 'FAILED_SCHEMA_CLEANUP_PENDING', 'tests_run': 0, 'schema_uuid_consumed': {},
        'expected_schemas': expected, 'class_counts': {}, 'denied_guard_events': [], 'source_unchanged': False}
    try:
        suite = unittest.TestSuite()
        for number, (name, classes, prefix) in enumerate(TARGETS):
            raw = (REPO/name).read_bytes()
            need(sha(raw) == manifest['files'][name]['sha256'], 'TEST_SOURCE_CHANGED')
            module = types.ModuleType('_strict107_'+str(number))
            module.__file__ = str(REPO/name)
            sys.modules[module.__name__] = module
            exec(compile(raw, module.__file__, 'exec', dont_inherit=True), module.__dict__)
            modules[name] = module
            if prefix:
                original = module.uuid4
                report['schema_uuid_consumed'][name] = False
                def first_schema(mod=module, original_uuid=original, key=name):
                    mod.uuid4 = original_uuid
                    report['schema_uuid_consumed'][key] = True
                    return nonce
                module.uuid4 = first_schema
            for class_name, count in classes:
                tests = unittest.defaultTestLoader.loadTestsFromTestCase(getattr(module, class_name))
                need(tests.countTestCases() == count, 'EXACT_CLASS_COUNT_REQUIRED')
                report['class_counts'][class_name] = count
                suite.addTests(tests)
        need(suite.countTestCases() == 107, 'EXACT_FULL_107_REQUIRED')
        # Keep assertLogs active. Raw unittest tracebacks stay only in this memory
        # buffer and are never uploaded or printed; failed test IDs are safe metadata.
        tested = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        report.update(tests_run=tested.testsRun, failures=len(tested.failures), errors=len(tested.errors),
            skipped=len(tested.skipped), expected_failures=len(tested.expectedFailures), unexpected_successes=len(tested.unexpectedSuccesses))
        report['failed_test_ids'] = [test.id() if re.fullmatch(r'[A-Za-z0-9_. ()]{1,240}', test.id()) else 'unrecognized_test_id'
                                     for test, _ in tested.failures+tested.errors]
        working_source(manifest)
        report['source_unchanged'] = True
        bound = all(report['schema_uuid_consumed'].get(name) is True
                    and getattr(getattr(modules[name], classes[0][0]), 'schema', None) == prefix+nonce.hex
                    for name, classes, prefix in TARGETS if prefix)
        if tested.wasSuccessful() and tested.testsRun == 107 and not tested.skipped and not tested.expectedFailures and not tested.unexpectedSuccesses and not denied and bound:
            report['status'] = 'TEST_ASSERTIONS_PASS_SCHEMA_CLEANUP_PENDING'
    except Exception as error:
        report['failure_kind'] = type(error).__name__ if re.fullmatch(r'[A-Za-z_]{1,64}', type(error).__name__) else 'Exception'
    finally:
        report['denied_guard_events'] = denied
        write_json(Path.cwd()/'child-result.json', report)
    return 0 if report['status'] == 'TEST_ASSERTIONS_PASS_SCHEMA_CLEANUP_PENDING' else 1


def run_child(arguments, cwd):
    env = {name: os.environ[name] for name in ('PATH', 'LANG', 'LC_ALL', 'TZ') if name in os.environ}
    process = subprocess.Popen(arguments, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pipes, lengths, started, minimum = None, [0, 0], time.monotonic(), None
    try:
        pipes = selectors.DefaultSelector()
        minimum = available_mib()
        for index, stream in enumerate((process.stdout, process.stderr)):
            pipes.register(stream, selectors.EVENT_READ, index)
        while pipes.get_map() or process.poll() is None:
            need(time.monotonic()-started < 300, 'CHILD_300_SECOND_DEADLINE')
            minimum = min(minimum, available_mib())
            need(minimum >= 320, 'AVAILABLE_MEMORY_BELOW_320_MIB')
            for key, _ in pipes.select(timeout=0.2):
                data = os.read(key.fileobj.fileno(), 8192)
                if not data:
                    pipes.unregister(key.fileobj)
                else:
                    lengths[key.data] += len(data)
                    need(lengths[key.data] <= 262144, 'CHILD_OUTPUT_BOUND')
        return process.wait(timeout=1), minimum, lengths
    finally:
        if pipes is not None:
            pipes.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        for stream in (process.stdout, process.stderr):
            stream.close()


def parent(args, manifest, manifest_pin):
    need(REPO == args.repo and ARTIFACTS == args.artifacts and SCRATCH.is_dir() and ARTIFACTS.is_dir()
         and not any(ARTIFACTS.iterdir()), 'FIXED_NEW_CI_PATHS_REQUIRED')
    need(available_mib() >= 600, 'INITIAL_MEMORY_BELOW_600_MIB')
    operational = git_source(manifest, args.expected_commit)
    inventory = linux_inventory(manifest_pin, args.expected_commit, operational)
    nonce = uuid4().hex
    schemas = [prefix+nonce for _, _, prefix in TARGETS if prefix]
    report = {'status': 'FAILED_OR_CLEANUP_UNRESOLVED', 'source_commit': args.expected_commit,
        'source_manifest_sha256': manifest_pin, 'runner_sha256': file_sha(SELF), 'expected_tests': 107,
        'tests_pass': False, 'exact_schemas_absent': False, 'cleanup_errors': [],
        'bootstrap_and_full_migration_chain_proven': False, 'real_provider_or_production_actions': False}
    tests_attempted = False
    try:
        connection, version = local_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_namespace WHERE nspname ~ '^(network_test_|customer_delivery_test_|lead_delivery_test_)[a-f0-9]{32}$'")
                need(cursor.fetchone()[0] == 0, 'OLD_SYNTHETIC_SCHEMAS_REFUSED')
        finally:
            connection.close()
        inventory['postgres_server_version_num'] = version
        need(available_mib() >= 600, 'PRE_CHILD_MEMORY_BELOW_600_MIB')
        work = SCRATCH/('strict107-'+nonce)
        work.mkdir(exist_ok=False)
        tests_attempted = True
        code, minimum, lengths = run_child([sys.executable, '-I', '-S', '-B', str(SELF), '--child', '--nonce', nonce,
            '--manifest-sha256', manifest_pin, '--expected-commit', args.expected_commit], work)
        need((work/'child-result.json').stat().st_size < 65536, 'BOUNDED_CHILD_AGGREGATE_REQUIRED')
        child_report = json.loads((work/'child-result.json').read_bytes())
        report.update(native_child_exit=code, minimum_available_mib=minimum, child_output_bytes=lengths, assertions=child_report)
        report['tests_pass'] = (code == 0 and child_report.get('status') == 'TEST_ASSERTIONS_PASS_SCHEMA_CLEANUP_PENDING'
            and type(child_report.get('tests_run')) is int and child_report['tests_run'] == 107
            and all(type(child_report.get(key)) is int and child_report[key] == 0 for key in
                    ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))
            and child_report.get('class_counts') == {name: count for _, classes, _ in TARGETS for name, count in classes}
            and child_report.get('expected_schemas') == {name: prefix+nonce for name, _, prefix in TARGETS if prefix}
            and child_report.get('schema_uuid_consumed') == {name: True for name, _, prefix in TARGETS if prefix}
            and child_report.get('source_unchanged') is True and child_report.get('denied_guard_events') == [])
    except Exception as error:
        value = error.args[0] if type(error) is ValueError and error.args else None
        report['failure_code'] = value if isinstance(value, str) and re.fullmatch(r'[A-Z0-9_]{1,90}', value) else 'CI_SETUP_OR_CHILD_FAILED'
    finally:
        if tests_attempted:
            try:
                connection, _ = local_connection()
                try:
                    with connection.cursor() as cursor:
                        for schema in schemas:
                            try:
                                need(re.fullmatch(r'(network_test_|customer_delivery_test_|lead_delivery_test_)'+nonce, schema), 'EXACT_OWNED_SCHEMA_REQUIRED')
                                cursor.execute('SELECT count(*) FROM pg_namespace WHERE nspname=%s', (schema,))
                                if cursor.fetchone()[0] == 1:
                                    cursor.execute('DROP SCHEMA "'+schema+'" CASCADE')
                                cursor.execute('SELECT count(*) FROM pg_namespace WHERE nspname=%s', (schema,))
                                need(cursor.fetchone()[0] == 0, 'OWNED_SCHEMA_REMAINS')
                            except Exception:
                                report['cleanup_errors'].append('EXACT_OWNED_SCHEMA_CLEANUP_UNRESOLVED')
                    report['exact_schemas_absent'] = not report['cleanup_errors']
                finally:
                    connection.close()
            except Exception:
                report['cleanup_errors'].append('EXACT_OWNED_SCHEMA_CLEANUP_UNRESOLVED')
        try:
            working_source(manifest)
            report['source_unchanged'] = True
        except Exception:
            report['cleanup_errors'].append('SOURCE_CHANGED')
        if report['tests_pass'] and report['exact_schemas_absent'] and not report['cleanup_errors']:
            report['status'] = 'PASS_107_ASSERTIONS_AND_EXACT_SCHEMA_CLEANUP_PG_CONTAINER_CLEANUP_PENDING'
        write_json(ARTIFACTS/'strict107-linux-inventory.json', inventory)
        write_json(ARTIFACTS/'strict107-result.json', report)
    print(json.dumps({'status': report['status'], 'tests': report.get('assertions', {}).get('tests_run'),
                      'bootstrap_and_full_migration_chain_proven': False}))
    return 0 if report['status'].startswith('PASS_107_') else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--artifacts', type=Path, default=ARTIFACTS)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--child', action='store_true')
    parser.add_argument('--nonce')
    parser.add_argument('--manifest-sha256')
    args = parser.parse_args()
    bootstrap_sites()
    manifest, manifest_pin = load_manifest()
    return child(args, manifest, manifest_pin) if args.child else parent(args, manifest, manifest_pin)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('{"status":"STRICT107_STOPPED_NO_ACCEPTANCE_CLAIM"}')
        raise SystemExit(1)
