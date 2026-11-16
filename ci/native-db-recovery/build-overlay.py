"""Project portable functions from the reviewed local source for public synthetic CI."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re


CAPTURE_SOURCE = 'a7a221dd971589b2a6fb1cd7fc9e34f663132202f56ce428c69b1290571761db'
FORMAT_SOURCE = 'd5a01eaf052ca03062f7de698936ce8e65726c2f30a85c5b3c0d3e45a94368aa'
CATALOG_SOURCE = '06b8e6c46068e10845b4e17122906c336cbc704804bb1809f1e2f4b71910920d'
CAPTURE_NODES = (
    'TABLES', 'UNSUPPORTED', 'MAX_DUMP', 'MAX_TOC', 'MAX_RECORDS_PER_TABLE', 'PROFILE_COUNTS',
    'Stop', 'require', 'pg_environment', 'private_process', 'checked_catalog', 'snapshot_plan',
    'validate_toc', 'pg_dump_credentials', 'capture',
)
FORMAT_NODES = ('MAGIC', 'seal', 'unseal')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_pinned(path, sha256):
    data = path.read_bytes()
    if digest(data) != sha256:
        raise ValueError('Fixture source hash changed')
    return data.decode('utf-8-sig').replace('\r\n', '\n')


def project(source, selected):
    imports, segments, lines = [], [], {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(ast.get_source_segment(source, node))
            continue
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        else:
            continue
        if name in selected:
            segments.append(ast.get_source_segment(source, node))
            lines[name] = [node.lineno, node.end_lineno]
    if set(lines) != set(selected):
        raise ValueError('Fixture function selection changed')
    return '\n'.join(imports) + '\n\n\n' + '\n\n\n'.join(segments) + '\n', lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('capture', 'format', 'catalog', 'target'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    capture, capture_lines = project(read_pinned(args.capture, CAPTURE_SOURCE), CAPTURE_NODES)
    archive, archive_lines = project(read_pinned(args.format, FORMAT_SOURCE), FORMAT_NODES)
    catalog = read_pinned(args.catalog, CATALOG_SOURCE)
    catalog, replacements = re.subn(r"('project_ref',\s*)'[a-z0-9]{20}'", r"\1'synthetic-native-db'", catalog)
    if replacements != 1:
        raise ValueError('Expected one project identity replacement')

    files = {'capture_fixture_source.py': capture, 'archive_fixture_source.py': archive, 'catalog.sql': catalog}
    for name, content in files.items():
        (args.target / name).write_bytes(content.encode('utf-8'))
    manifest = {
        'reviewed_capture_sha256': CAPTURE_SOURCE,
        'reviewed_archive_format_sha256': FORMAT_SOURCE,
        'reviewed_catalog_sha256': CATALOG_SOURCE,
        'capture_original_lines_inclusive': capture_lines,
        'archive_original_lines_inclusive': archive_lines,
        'catalog_transform': 'UTF-8 LF without BOM; exactly one project_ref literal replaced with synthetic-native-db',
        'transformation': 'Selected AST source segments and imports only; owner paths, identifiers, pins and entry points omitted',
        'files_sha256_lf': {name: digest(content.encode('utf-8')) for name, content in files.items()},
    }
    (args.target / 'overlay-manifest.json').write_bytes((json.dumps(manifest, indent=2) + '\n').encode('utf-8'))


if __name__ == '__main__':
    main()
