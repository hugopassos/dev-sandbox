#!/usr/bin/env python3

import sys
from pathlib import Path

from prepare_workspace import (
    MAX_TOTAL_SIZE,
    excluded,
)
from review_workspace import (
    inspect_file,
    load_manifest,
    valid_relative,
    workspace_files,
)
from safe_files import read_regular_file, UnsafeFileError
from prepare_workspace import MAX_FILE_SIZE
from workspace_ignore import IGNORE_FILE, ignored, load_ignore_rules


def audit(source_path):
    source, workspace, _ = load_manifest(source_path)
    ignore_rules = load_ignore_rules(source)

    files, unsafe_paths = workspace_files(workspace)
    problems = []
    total_size = 0

    for name in sorted(unsafe_paths):
        problems.append((name, 'link simbólico ou caminho inseguro'))

    for name in sorted(files):
        if not valid_relative(name) or excluded(Path(name)):
            problems.append((name, 'nome ou caminho proibido'))
            continue

        if ignored(Path(name), ignore_rules):
            problems.append((name, f'excluído por {IGNORE_FILE}. Prepare uma nova cópia'))
            continue

        state, _ = inspect_file(
            workspace,
            name,
            scan_secrets=True,
        )

        if state != 'regular':
            problems.append((name, 'conteúdo ou tipo não permitido'))
            continue

        try:
            data = read_regular_file(
                workspace,
                name,
                MAX_FILE_SIZE,
                require_single_link=True,
            )
        except (OSError, UnsafeFileError):
            problems.append((name, 'falha na leitura segura'))
            continue

        total_size += len(data)

        if total_size > MAX_TOTAL_SIZE:
            problems.append((
                name,
                'limite total de arquivos excedido',
            ))
            break

    if problems:
        print(
            '\nAuditoria do workspace: FALHA',
            file=sys.stderr,
        )

        for name, reason in problems:
            print(
                f'  {name}: {reason}',
                file=sys.stderr,
            )

        return 1

    print(
        f'Auditoria do workspace: OK '
        f'({len(files)} arquivos verificados)'
    )
    return 0


def main():
    if len(sys.argv) != 2:
        print(
            'Uso: audit_workspace.py <repositorio-original>',
            file=sys.stderr,
        )
        return 1

    try:
        return audit(Path(sys.argv[1]))
    except Exception as error:
        print(f'ERRO na auditoria: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
