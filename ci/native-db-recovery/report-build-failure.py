"""Classify private BuildKit output without copying raw lines into Actions logs."""

from pathlib import Path
import re
import sys


MAX_TAIL = 512 * 1024
STAGE = rb'^#\d+ [0-9.]+ NATIVE_DB_INSTALL_STAGE (apt_index|apt_packages|python_venv|python_wheels)\s*$'
CLASSES = (
    (b'do not match the hashes|hashes are required', 'wheel_hash'),
    (b'no matching distribution|could not find a version|requires a different python', 'wheel_unavailable'),
    (b'resolutionimpossible|conflicting dependencies', 'wheel_dependency_conflict'),
    (b'certificate_verify_failed|certificate verification|sslcertverificationerror', 'repository_tls'),
    (b'gpg error|no_pubkey|expkeysig|repository .* is not signed|invalid signature', 'repository_signature'),
    (b'unable to locate package|has no installation candidate', 'apt_package_unavailable'),
    (b'ensurepip|has no attribute .*venv|no module named venv', 'python_venv_bootstrap'),
    (b'temporary failure resolving|could not resolve|could not connect|connection refused|timed out|failed to fetch',
     'repository_connection'),
    (b'no space left on device', 'runner_disk'),
    (b'permission denied|operation not permitted', 'runner_permission'),
)


def classify(log, status):
    markers = list(re.finditer(STAGE, log, re.MULTILINE))
    if markers:
        phase = markers[-1][1].decode('ascii')
        tail = log[markers[-1].end():].lower()
    else:
        phase = 'base_image_or_prepare'
        tail = log.lower()
    if status == 124:
        return phase, 'timeout'
    for pattern, label in CLASSES:
        if re.search(pattern, tail):
            return phase, label
    return phase, 'unclassified'


def main():
    status = int(sys.argv[2])
    with Path(sys.argv[1]).open('rb') as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - MAX_TAIL))
        phase, failure = classify(stream.read(MAX_TAIL), status)
    print(f'NATIVE_DB_RECOVERY_BUILD status=FAIL phase={phase} class={failure} exit={status}')


if __name__ == '__main__':
    main()
