#!/usr/bin/env python3

import json
import shutil
import subprocess
import sys
from pathlib import Path

from prepare_workspace import BASELINES_DIR
from review_workspace import load_manifest, valid_relative


class ResolveError(Exception):
    pass


def regular_file(root, relative):
    path = root

    for part in Path(relative).parts:
        path = path / part

        if path.is_symlink():
            raise ResolveError(f'Link simbólico não permitido: {relative}')

    if not path.is_file():
        raise ResolveError(f'Arquivo não encontrado: {relative}')

    return path


def resolve(source_path, session_path):
    source, workspace, manifest = load_manifest(source_path)

    baseline = BASELINES_DIR / workspace.name

    if manifest.get('baseline') != str(baseline):
        raise ResolveError('Baseline inconsistente.')

    session = session_path.expanduser().resolve(strict=True)

    expected_parent = (
        Path.home()
        / '.local/share/dev-sandbox/merges'
        / workspace.name
    ).resolve(strict=True)

    if session.parent != expected_parent:
        raise ResolveError('Sessão não pertence ao projeto informado.')

    report_path = regular_file(session, 'merge-report.json')
    report = json.loads(report_path.read_text(encoding='utf-8'))

    if (
        report.get('source') != str(source)
        or report.get('workspace') != str(workspace)
        or report.get('baseline') != str(baseline)
        or not isinstance(report.get('files'), list)
    ):
        raise ResolveError('Relatório inconsistente.')

    conflicts = [
        item['path']
        for item in report['files']
        if item.get('status') == 'CONFLICT'
    ]

    if not conflicts:
        print('Nenhum conflito de conteúdo registrado nesta sessão.')
        return 0

    if not shutil.which('code'):
        raise ResolveError('Comando code não encontrado no PATH.')

    files = []

    for name in conflicts:
        if not valid_relative(name):
            raise ResolveError(f'Caminho inválido: {name!r}')

        files.append((
            regular_file(source, name),
            regular_file(workspace, name),
            regular_file(baseline, name),
            regular_file(session / 'files', name),
        ))

    print(f'\nSessão: {session}')
    print(f'Conflitos encontrados: {len(files)}')

    for index, (original, codex, base, result) in enumerate(files, 1):
        print(f'\n[{index}/{len(files)}] {result.name}')

        subprocess.run(
            [
                'code',
                '--wait',
                '--merge',
                str(original),
                str(codex),
                str(base),
                str(result),
            ],
            check=True,
        )

    print('\nEditor de merge encerrado.')
    print('Revise os arquivos resultantes antes de incorporá-los.')
    print('O repositório original não foi modificado pela ferramenta.')

    return 0


def main():
    if len(sys.argv) != 3:
        print('Uso: resolve_workspace.py <repositorio> <sessao>')
        return 1

    try:
        return resolve(Path(sys.argv[1]), Path(sys.argv[2]))
    except (
        ResolveError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.CalledProcessError,
    ) as error:
        print(f'ERRO: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
