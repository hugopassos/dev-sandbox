#!/usr/bin/env python3

import os
import stat
import subprocess
import sys
from pathlib import Path

from prepare_workspace import BASELINES_DIR, MANIFESTS_DIR, WORKSPACES_DIR
from review_workspace import load_manifest


def fail(message):
    print(f'ERRO: {message}', file=sys.stderr)
    sys.exit(1)


def check_private_directory(path):
    if path.is_symlink() or not path.is_dir():
        fail(f'Diretório privado inválido: {path}')

    info = path.stat()

    if info.st_uid != os.getuid():
        fail(f'Diretório não pertence ao usuário: {path}')

    if stat.S_IMODE(info.st_mode) & 0o022:
        fail(f'Diretório permite escrita por terceiros: {path}')


def locate(source):
    source = source.expanduser().resolve(strict=True)

    if not source.is_dir():
        fail('O projeto informado não é um diretório.')

    # Não aceitar diretórios arbitrários nem subdiretórios do Git.
    result = subprocess.run(
        ['git', '-C', str(source), 'rev-parse', '--show-toplevel'],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        fail(
            'O diretório informado não é um repositório Git válido. '
            'Informe a raiz de um projeto Git e execute '
            '"dev-sandbox prepare <diretorio>" antes de utilizá-lo.'
        )

    git_root = Path(result.stdout.strip()).resolve(strict=True)

    if git_root != source:
        fail('Informe a raiz do repositório Git original.')

    # Reutiliza a associação entre origem, manifesto e workspace.
    _, workspace, manifest = load_manifest(source)

    baseline = BASELINES_DIR / workspace.name

    if manifest.get('baseline') != str(baseline):
        fail('Manifesto não possui uma baseline válida.')

    for directory in (
        WORKSPACES_DIR,
        MANIFESTS_DIR,
        BASELINES_DIR,
        workspace,
        baseline,
    ):
        check_private_directory(directory)

    manifest_path = MANIFESTS_DIR / f'{workspace.name}.json'

    if manifest_path.is_symlink():
        fail('Manifesto não pode ser um link simbólico.')

    manifest_info = manifest_path.stat()

    if (
        manifest_info.st_uid != os.getuid()
        or not stat.S_ISREG(manifest_info.st_mode)
        or stat.S_IMODE(manifest_info.st_mode) & 0o077
    ):
        fail('Permissões do manifesto inválidas.')

    # Apenas o caminho autorizado é enviado ao launcher.
    print(workspace)


def main():
    if len(sys.argv) != 2:
        fail('Uso: locate_workspace.py <repositorio-original>')

    try:
        locate(Path(sys.argv[1]))
    except (
        OSError,
        subprocess.CalledProcessError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        fail(str(error))


if __name__ == '__main__':
    main()
