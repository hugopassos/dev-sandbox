#!/usr/bin/env python3

import difflib
import hashlib
import itertools
import sys
from pathlib import Path

from prepare_workspace import (
    MAX_FILE_SIZE,
    SECRET_PATTERNS,
    excluded,
)
from review_workspace import (
    inspect_file,
    load_manifest,
    valid_relative,
    workspace_files,
)
from safe_files import read_regular_file, UnsafeFileError
from workspace_ignore import ignored, load_ignore_rules


MAX_DIFF_FILES = 100
MAX_DIFF_LINES = 300


def safe_text(root, name, expected_hash=None):
    try:
        data = read_regular_file(root, name, MAX_FILE_SIZE)
        content = data.decode('utf-8')
    except (OSError, UnsafeFileError, UnicodeError):
        return None

    if '\0' in content:
        return None

    # Impede que arquivos imprimam sequências de controle
    # capazes de manipular a exibição do terminal.
    if any(ord(char) < 32 and char not in '\n\r\t'
           for char in content):
        return None

    if '\x7f' in content or '\x1b' in content:
        return None

    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        return None

    if expected_hash is not None:
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_hash:
            return None

    return content


def display_diff(name, original, modified):
    before = [] if original is None else original.splitlines(
        keepends=True
    )
    after = [] if modified is None else modified.splitlines(
        keepends=True
    )

    lines = difflib.unified_diff(
        before,
        after,
        fromfile=f'a/{name}' if original is not None else '/dev/null',
        tofile=f'b/{name}' if modified is not None else '/dev/null',
        lineterm='\n',
    )

    print(f'\n--- Alterações: {name} ---')

    shown = 0

    for line in itertools.islice(lines, MAX_DIFF_LINES + 1):
        if shown >= MAX_DIFF_LINES:
            print('\n[DIFF TRUNCADO]')
            break

        sys.stdout.write(line)

        if not line.endswith('\n'):
            sys.stdout.write('\n')

        shown += 1


def diff_workspace(source_path):
    source, workspace, manifest = load_manifest(source_path)
    ignore_rules = load_ignore_rules(source)

    baseline = {
        name: metadata['sha256']
        for name, metadata in manifest['files'].items()
    }

    current, unsafe_paths = workspace_files(workspace)

    if unsafe_paths:
        print('Arquivos ou diretórios inseguros encontrados:')
        for name in sorted(unsafe_paths):
            print(f'  {name}')
        print('Nenhum diff será exibido.')
        return 2

    names = sorted(set(baseline) | current)

    if len(names) > 10000:
        print('Quantidade de arquivos excedida.')
        return 2

    changes = []
    blocked = []

    for name in names:
        if not valid_relative(name) or excluded(Path(name)):
            blocked.append((name, 'caminho proibido'))
            continue

        if ignored(Path(name), ignore_rules):
            continue

        workspace_state, workspace_hash = inspect_file(
            workspace, name, scan_secrets=True
        )

        source_state, source_hash = inspect_file(
            source, name, scan_secrets=True
        )

        if 'unsafe' in (workspace_state, source_state):
            blocked.append((name, 'arquivo inseguro'))
            continue

        if name in baseline:
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
                continue

            if not source_unchanged:
                blocked.append((name, 'conflito no original'))
                continue

            status = (
                'EXCLUÍDO'
                if workspace_state == 'missing'
                else 'MODIFICADO'
            )

        else:
            if workspace_state != 'regular':
                blocked.append((name, 'workspace inválido'))
                continue

            if source_state != 'missing':
                blocked.append((name, 'arquivo já existe no original'))
                continue

            status = 'NOVO'

        changes.append((
            status,
            name,
            source_hash,
            workspace_hash,
        ))

    if len(changes) > MAX_DIFF_FILES:
        print('Quantidade de arquivos alterados excedida.')
        return 2

    print(f'\nOrigem: {source}')
    print(f'Workspace: {workspace}')

    displayed = 0

    for status, name, source_hash, workspace_hash in changes:
        original = None
        modified = None

        if status != 'NOVO':
            original = safe_text(source, name, source_hash)

            if original is None:
                blocked.append((name, 'leitura do original falhou'))
                continue

        if status != 'EXCLUÍDO':
            modified = safe_text(
                workspace, name, workspace_hash
            )

            if modified is None:
                blocked.append((name, 'leitura do workspace falhou'))
                continue

        # Confere novamente os hashes imediatamente antes de exibir.
        if status != 'NOVO':
            state, digest = inspect_file(source, name, True)
            if state != 'regular' or digest != source_hash:
                blocked.append((name, 'original mudou durante a revisão'))
                continue

        if status != 'EXCLUÍDO':
            state, digest = inspect_file(workspace, name, True)
            if state != 'regular' or digest != workspace_hash:
                blocked.append((name, 'workspace mudou durante a revisão'))
                continue

        print(f'\n[{status}] {name}')
        display_diff(name, original, modified)
        displayed += 1

    print(f'\nDiffs exibidos: {displayed}')

    if blocked:
        print('\nArquivos não exibidos:')
        for name, reason in blocked:
            print(f'  {name}: {reason}')

        return 2

    if not changes:
        print('Nenhuma alteração elegível encontrada.')

    return 0


def main():
    if len(sys.argv) != 2:
        print('Uso: diff_workspace.py <repositorio-original>')
        return 1

    try:
        return diff_workspace(Path(sys.argv[1]))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'ERRO: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
