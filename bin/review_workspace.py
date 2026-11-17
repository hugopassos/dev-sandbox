#!/usr/bin/env python3

import hashlib
import json
import os
import re
import stat
import sys
from collections import Counter
from pathlib import Path
from safe_files import read_regular_file, UnsafeFileError

from prepare_workspace import (
    MANIFESTS_DIR,
    WORKSPACES_DIR,
    MAX_FILE_SIZE,
    SECRET_PATTERNS,
    excluded,
)


MAX_FILES = 10000
HASH_PATTERN = re.compile(r'^[0-9a-f]{64}$')


def fail(message):
    print(f'ERRO: {message}', file=sys.stderr)
    sys.exit(1)


def valid_relative(name):
    if not isinstance(name, str) or not name or '\0' in name:
        return False

    if '\\' in name:
        return False

    path = Path(name)

    return (
        not path.is_absolute()
        and all(part not in ('.', '..') for part in name.split('/'))
        and not name.startswith('/')
        and '//' not in name
        and not name.endswith('/')
    )


def inspect_file(root, relative, scan_secrets=False):
    try:
        data = read_regular_file(
            root,
            relative,
            MAX_FILE_SIZE,
        )

    except FileNotFoundError:
        return 'missing', None

    except UnsafeFileError:
        return 'unsafe', None

    digest = hashlib.sha256(data).hexdigest()

    if scan_secrets:
        try:
            content = data.decode('utf-8')
        except UnicodeDecodeError:
            return 'unsafe', None

        if '\0' in content:
            return 'unsafe', None

        if any(
            pattern.search(content)
            for pattern in SECRET_PATTERNS
        ):
            return 'unsafe', None

    return 'regular', digest


def workspace_files(workspace):
    names = set()
    unsafe = set()

    for current, directories, files in os.walk(
        workspace,
        topdown=True,
        followlinks=False,
    ):
        relative_dir = Path(current).relative_to(workspace)

        for directory in list(directories):
            path = Path(current) / directory
            relative = (relative_dir / directory).as_posix()

            if path.is_symlink():
                unsafe.add(relative)
                directories.remove(directory)

        for filename in files:
            relative = (relative_dir / filename).as_posix()

            if valid_relative(relative):
                names.add(relative)
            else:
                unsafe.add(relative)

        if len(names) + len(unsafe) > MAX_FILES:
            fail('Quantidade máxima de arquivos excedida.')

    return names, unsafe


def load_manifest(source):
    source = source.expanduser().resolve(strict=True)

    if not source.is_dir():
        fail('A origem precisa ser um diretório.')

    repository_hash = hashlib.sha256(
        os.fsencode(str(source))
    ).hexdigest()[:12]

    name = f'{source.name}-{repository_hash}'

    manifest_path = MANIFESTS_DIR / f'{name}.json'
    workspace = WORKSPACES_DIR / name

    if manifest_path.is_symlink() or not manifest_path.is_file():
        fail('Manifesto não encontrado ou inválido.')

    if workspace.is_symlink() or not workspace.is_dir():
        fail('Workspace não encontrado ou inválido.')

    with manifest_path.open(encoding='utf-8') as file:
        manifest = json.load(file)

    if (
        manifest.get('schema_version') != 1
        or manifest.get('source') != str(source)
        or manifest.get('workspace') != str(workspace)
        or not isinstance(manifest.get('files'), dict)
    ):
        fail('Manifesto inconsistente com o repositório informado.')

    for name, metadata in manifest['files'].items():
        if (
            not valid_relative(name)
            or not isinstance(metadata, dict)
            or not HASH_PATTERN.fullmatch(
                str(metadata.get('sha256', ''))
            )
        ):
            fail('Manifesto contém um registro inválido.')

    return source, workspace, manifest


def review(source):
    source, workspace, manifest = load_manifest(source)

    baseline = {
        name: metadata['sha256']
        for name, metadata in manifest['files'].items()
    }

    current, unsafe_paths = workspace_files(workspace)

    records = []

    for name in sorted(set(baseline) | current):
        if not valid_relative(name):
            records.append(('INSEGURO', name))
            continue

        if excluded(Path(name)):
            records.append(('INSEGURO', name))
            continue

        workspace_state, workspace_hash = inspect_file(
            workspace, name, scan_secrets=True
        )

        source_state, source_hash = inspect_file(
            source, name
        )

        if workspace_state == 'unsafe' or source_state == 'unsafe':
            status = 'INSEGURO'

        elif name in baseline:
            initial_hash = baseline[name]

            workspace_changed = (
                workspace_state == 'missing'
                or workspace_hash != initial_hash
            )

            source_unchanged = (
                source_state == 'regular'
                and source_hash == initial_hash
            )

            if not workspace_changed:
                status = (
                    'INALTERADO'
                    if source_unchanged
                    else 'ORIGINAL ALTERADO'
                )
            elif not source_unchanged:
                status = 'CONFLITO'
            elif workspace_state == 'missing':
                status = 'EXCLUÍDO'
            else:
                status = 'MODIFICADO'

        else:
            if workspace_state != 'regular':
                status = 'INSEGURO'
            elif source_state != 'missing':
                status = 'CONFLITO'
            else:
                status = 'NOVO'

        records.append((status, name))

    for name in sorted(unsafe_paths):
        records.append(('INSEGURO', name))

    counts = Counter(status for status, _ in records)

    print(f'\nOrigem: {source}')
    print(f'Workspace: {workspace}')
    print('\nRevisão de alterações:\n')

    for status, name in records:
        if status != 'INALTERADO':
            print(f'  [{status}] {name}')

    print('\nResumo:')
    for status in (
        'MODIFICADO',
        'NOVO',
        'EXCLUÍDO',
        'CONFLITO',
        'INSEGURO',
        'ORIGINAL ALTERADO',
        'INALTERADO',
    ):
        print(f'  {status}: {counts[status]}')

    print('\nNenhum arquivo foi modificado.')

    if counts['CONFLITO'] or counts['INSEGURO']:
        print('Há conflitos ou arquivos inseguros para investigar.')
        return 2

    return 0


def main():
    if len(sys.argv) != 2:
        fail('Uso: review_workspace.py <repositorio-original>')

    try:
        return review(Path(sys.argv[1]))
    except (OSError, ValueError, KeyError, TypeError) as error:
        fail(str(error))


if __name__ == '__main__':
    sys.exit(main())
