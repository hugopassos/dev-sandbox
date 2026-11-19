#!/usr/bin/env python3

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from prepare_workspace import (
    BASELINES_DIR,
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


MERGES_DIR = (
    Path.home() / '.local/share/dev-sandbox/merges'
)

MAX_MERGED_SIZE = 4 * 1024 * 1024


class MergeError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_read(root, name, expected=None):
    try:
        data = read_regular_file(root, name, MAX_FILE_SIZE)
        content = data.decode('utf-8')
    except (OSError, UnsafeFileError, UnicodeError) as error:
        raise MergeError(f'Leitura insegura: {name}') from error

    if '\0' in content or '\x1b' in content:
        raise MergeError(f'Conteúdo não permitido: {name}')

    if any(
        pattern.search(content)
        for pattern in SECRET_PATTERNS
    ):
        raise MergeError(f'Possível credencial: {name}')

    if expected is not None and digest(data) != expected:
        raise MergeError(f'Hash inconsistente: {name}')

    return data


def merge_contents(original, baseline, codex):
    """
    git merge-file recebe:
      1. Nossa versão: original atual
      2. Base comum: baseline
      3. Outra versão: Codex

    Usa -p para não modificar os arquivos de entrada.
    """
    with tempfile.TemporaryDirectory(
        prefix='codex-merge-input-'
    ) as directory:
        root = Path(directory)

        inputs = [
            ('original', original),
            ('baseline', baseline),
            ('codex', codex),
        ]

        paths = []

        for filename, content in inputs:
            path = root / filename
            path.write_bytes(content)
            path.chmod(0o600)
            paths.append(str(path))

        result = subprocess.run(
            [
                'git', 'merge-file', '-p',
                '-L', 'ORIGINAL',
                '-L', 'BASELINE',
                '-L', 'CODEX',
                *paths,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    if result.returncode < 0 or result.returncode >= 128:
        raise MergeError(
            'Git não conseguiu executar o merge: '
            + result.stderr.decode('utf-8', errors='replace')
        )

    if len(result.stdout) > MAX_MERGED_SIZE:
        raise MergeError('Resultado do merge excedeu o limite.')

    # Rejeita padrões sensíveis também no resultado.
    content = result.stdout.decode('utf-8')
    if (
        '\0' in content
        or '\x1b' in content
        or any(pattern.search(content) for pattern in SECRET_PATTERNS)
    ):
        raise MergeError('Resultado contém conteúdo não permitido.')

    return result.stdout, result.returncode != 0


def write_result(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)

    with path.open('xb') as file:
        file.write(data)

    path.chmod(0o600)


def produce_merge(source_path):
    source, workspace, manifest = load_manifest(source_path)
    ignore_rules = load_ignore_rules(source)

    expected_baseline = BASELINES_DIR / workspace.name

    if manifest.get('baseline') != str(expected_baseline):
        raise MergeError('Baseline ausente ou inconsistente.')

    if (
        expected_baseline.is_symlink()
        or not expected_baseline.is_dir()
    ):
        raise MergeError('Diretório de baseline inválido.')

    baseline = {
        name: metadata['sha256']
        for name, metadata in manifest['files'].items()
    }

    current, unsafe_paths = workspace_files(workspace)

    if unsafe_paths:
        raise MergeError(
            f'Workspace contém {len(unsafe_paths)} '
            'caminho(s) inseguro(s).'
        )

    # Um merge antigo nunca é sobrescrito.
    MERGES_DIR.mkdir(parents=True, exist_ok=True)
    MERGES_DIR.chmod(0o700)

    project_dir = MERGES_DIR / workspace.name

    if project_dir.is_symlink():
        raise MergeError('Diretório de merges inválido.')

    project_dir.mkdir(mode=0o700, exist_ok=True)

    if not project_dir.is_dir():
        raise MergeError('Diretório de merges inválido.')

    session = Path(tempfile.mkdtemp(
        prefix='session-',
        dir=project_dir,
    ))
    session.chmod(0o700)

    output_dir = session / 'files'
    output_dir.mkdir(mode=0o700)

    records = []

    try:
        for name in sorted(set(baseline) | current):
            if not valid_relative(name) or excluded(Path(name)):
                raise MergeError(f'Caminho proibido: {name!r}')

            if ignored(Path(name), ignore_rules):
                continue

            initial_hash = baseline.get(name)

            if initial_hash is not None:
                baseline_data = checked_read(
                    expected_baseline, name, initial_hash
                )
            else:
                baseline_data = None

            workspace_state, workspace_hash = inspect_file(
                workspace, name, scan_secrets=True
            )

            source_state, source_hash = inspect_file(
                source, name, scan_secrets=True
            )

            if 'unsafe' in (workspace_state, source_state):
                raise MergeError(f'Arquivo inseguro: {name!r}')

            if initial_hash is not None:
                changed_by_codex = (
                    workspace_state == 'missing'
                    or workspace_hash != initial_hash
                )

                if not changed_by_codex:
                    continue

                if workspace_state == 'regular':
                    codex_data = checked_read(
                        workspace, name, workspace_hash
                    )
                else:
                    codex_data = None

                if source_state == 'regular':
                    original_data = checked_read(
                        source, name, source_hash
                    )
                else:
                    original_data = None

                if original_data == baseline_data:
                    if codex_data is None:
                        status = 'DELETE'
                        result_data = None
                    else:
                        status = 'READY'
                        result_data = codex_data

                elif codex_data is None and original_data is None:
                    # Ambos excluíram o arquivo.
                    continue

                elif original_data is None or codex_data is None:
                    status = 'MANUAL'
                    result_data = None

                else:
                    result_data, conflict = merge_contents(
                        original_data,
                        baseline_data,
                        codex_data,
                    )
                    status = 'CONFLICT' if conflict else 'MERGED'

            else:
                if workspace_state != 'regular':
                    raise MergeError(f'Arquivo novo inválido: {name!r}')

                codex_data = checked_read(
                    workspace, name, workspace_hash
                )

                if source_state == 'missing':
                    status = 'CREATE'
                    result_data = codex_data
                else:
                    status = 'MANUAL'
                    result_data = None

            if result_data is not None:
                write_result(output_dir, name, result_data)

            records.append({
                'path': name,
                'status': status,
                'output': name if result_data is not None else None,
            })

        report = {
            'schema_version': 1,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'source': str(source),
            'workspace': str(workspace),
            'baseline': str(expected_baseline),
            'files': records,
        }

        report_path = session / 'merge-report.json'

        with report_path.open('x', encoding='utf-8') as file:
            json.dump(report, file, indent=2, ensure_ascii=False)
            file.write('\n')

        report_path.chmod(0o600)

    except Exception:
        if session.exists():
            shutil.rmtree(session)
        raise

    print(f'\nOrigem: {source}')
    print(f'Sessão: {session}')
    print(f'Arquivos: {output_dir}')
    print(f'Relatório: {report_path}')
    print('\nArquivos analisados:\n')

    for record in records:
        print(f"  [{record['status']}] {record['path']}")

    conflicts = sum(
        record['status'] in ('CONFLICT', 'MANUAL')
        for record in records
    )

    print(f'\nTotal de operações: {len(records)}')
    print(f'Conflitos ou decisões manuais: {conflicts}')
    print('\nO repositório original não foi modificado.')
    print('Revise o resultado antes de incorporar alterações.')

    return 2 if conflicts else 0


def main():
    if len(sys.argv) != 2:
        print('Uso: merge_workspace.py <repositorio-original>')
        return 1

    try:
        return produce_merge(Path(sys.argv[1]))
    except (
        MergeError,
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        print(f'ERRO: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
