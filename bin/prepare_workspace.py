#!/usr/bin/env python3

import fnmatch
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import json
from datetime import datetime, timezone
from pathlib import Path
from safe_files import read_regular_file, UnsafeFileError


WORKSPACES_DIR = (
    Path.home() / '.local/share/codex-isolated/workspaces'
)

MANIFESTS_DIR = (
    Path.home() / '.local/share/codex-isolated/manifests'
)

BASELINES_DIR = (
    Path.home() / '.local/share/codex-isolated/baselines'
)

MAX_FILE_SIZE = 2 * 1024 * 1024
MAX_TOTAL_SIZE = 50 * 1024 * 1024

EXCLUDED_NAMES = (
    '.env',
    '.env.*',
    '.git',
    '.ssh',
    '.aws',
    '.npmrc',
    '.pypirc',
    '.netrc',
    '*.pem',
    '*.key',
    '*.p12',
    '*.pfx',
    '*.csv',
    '*.xlsx',
    '*.xlsm',
    '*.sqlite',
    '*.sqlite3',
    '*.db',
    '*.tfstate',
    '*.tfstate.*',
    '*credentials*',
    '*secret*',
    '*token*',
    '*password*',
    'id_rsa*',
    'id_ed25519*',
)

SECRET_PATTERNS = (
    re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}\b'),
    re.compile(r'\bsk-ant-[A-Za-z0-9_-]{15,}\b'),
    re.compile(r'\bsk-proj-[A-Za-z0-9_-]{15,}\b'),
    re.compile(
        r'(?im)^\s*(?:export\s+)?'
        r'(?:password|secret_key|api_key|access_token|'
        r'client_secret|private_key)\s*[:=]\s*'
        r'["\']?[^\s#"\']{8,}'
    ),
)


def fail(message):
    print(f'ERRO: {message}', file=sys.stderr)
    sys.exit(1)


def git_files(repo):
    result = subprocess.run(
        ['git', '-C', str(repo), 'ls-files', '--cached', '-z', '--full-name'],
        capture_output=True,
        check=True,
    )

    return [
        os.fsdecode(name)
        for name in result.stdout.split(b'\0')
        if name
    ]


def excluded(path):
    return any(
        fnmatch.fnmatchcase(part.lower(), pattern)
        for part in path.parts
        for pattern in EXCLUDED_NAMES
    )


def has_symlink_component(repo, relative):
    current = repo

    for part in relative.parts:
        current = current / part

        if current.is_symlink():
            return True

    return False


def create_manifest(
    repo,
    destination,
    copied,
    skipped,
    baseline_destination,
):
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.chmod(0o700)

    manifest_path = MANIFESTS_DIR / f'{destination.name}.json'

    manifest = {
        'schema_version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source': str(repo),
        'workspace': str(destination),
        'files': {},
        'excluded_files': [name for name, _ in skipped],
        'baseline': str(baseline_destination),
    }

    for name in copied:
        workspace_path = destination / name
        baseline_path = baseline_destination / name

        workspace_hash = hashlib.sha256(
            workspace_path.read_bytes()
        ).hexdigest()

        baseline_hash = hashlib.sha256(
            baseline_path.read_bytes()
        ).hexdigest()

        if workspace_hash != baseline_hash:
            raise ValueError(
                f'Baseline inconsistente: {name}'
            )

        manifest['files'][name] = {
            'sha256': baseline_hash,
        }

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL

    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW

    fd = os.open(manifest_path, flags, 0o600)

    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        json.dump(manifest, file, indent=2, ensure_ascii=False)
        file.write('\n')

    return manifest_path


def prepare(repo):
    repo = repo.expanduser().resolve(strict=True)

    if not repo.is_dir():
        fail('O caminho informado não é um diretório.')

    git_root = subprocess.run(
        ['git', '-C', str(repo), 'rev-parse', '--show-toplevel'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    if Path(git_root).resolve() != repo:
        fail('Informe a raiz do repositório Git.')

    if repo == Path.home() or repo == Path('/'):
        fail('Diretório de origem muito abrangente.')

    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACES_DIR.chmod(0o700)

    repo_hash = hashlib.sha256(
        os.fsencode(str(repo))
    ).hexdigest()[:12]

    destination = WORKSPACES_DIR / f'{repo.name}-{repo_hash}'

    if destination.exists() or destination.is_symlink():
        fail(
            f'O workspace já existe: {destination}\n'
            'Não será sobrescrito.'
        )

    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    BASELINES_DIR.chmod(0o700)

    baseline_destination = BASELINES_DIR / destination.name

    if (
        baseline_destination.exists()
        or baseline_destination.is_symlink()
    ):
        fail(
            f'A baseline já existe: {baseline_destination}\n'
            'Não será sobrescrita.'
        )

    staging = Path(tempfile.mkdtemp(
        prefix='.preparing-',
        dir=WORKSPACES_DIR,
    ))

    baseline_staging = Path(tempfile.mkdtemp(
        prefix='.baseline-',
        dir=BASELINES_DIR,
    ))

    baseline_staging.chmod(0o700)

    copied = []
    skipped = []
    suspicious = []
    total_size = 0

    try:
        for relative_name in git_files(repo):
            relative = Path(relative_name)

            if (
                relative.is_absolute()
                or '..' in relative.parts
                or not relative.parts
            ):
                fail('O Git retornou um caminho inválido.')

            if excluded(relative):
                skipped.append((relative_name, 'nome excluído'))
                continue

            if has_symlink_component(repo, relative):
                skipped.append((relative_name, 'link simbólico'))
                continue

            source = repo / relative

            try:
                source_stat = source.lstat()
            except FileNotFoundError:
                skipped.append((relative_name, 'arquivo ausente'))
                continue

            if not stat.S_ISREG(source_stat.st_mode):
                skipped.append((relative_name, 'arquivo não regular'))
                continue

            if source_stat.st_size > MAX_FILE_SIZE:
                skipped.append((relative_name, 'arquivo muito grande'))
                continue

            try:
                data = read_regular_file(
                    repo,
                    relative_name,
                    MAX_FILE_SIZE,
                )
            except FileNotFoundError:
                fail(f'Arquivo desapareceu durante a preparação: {relative_name}')
            except UnsafeFileError as error:
                fail(f'Leitura insegura de {relative_name}: {error}')

            if len(data) > MAX_FILE_SIZE:
                skipped.append((relative_name, 'arquivo muito grande'))
                continue

            try:
                content = data.decode('utf-8')
            except UnicodeDecodeError:
                skipped.append((relative_name, 'arquivo não textual'))
                continue

            if '\0' in content:
                skipped.append((relative_name, 'arquivo binário'))
                continue

            if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                suspicious.append(relative_name)
                continue

            total_size += len(data)

            if total_size > MAX_TOTAL_SIZE:
                fail('Limite total de 50 MiB excedido.')

            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.parent.chmod(0o700)
            target.write_bytes(data)
            target.chmod(0o600)

            baseline_target = baseline_staging / relative

            baseline_target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            baseline_target.parent.chmod(0o700)

            baseline_target.write_bytes(data)
            baseline_target.chmod(0o600)

            copied.append(relative_name)

        if suspicious:
            print('\nArquivos com conteúdo suspeito:')
            for name in suspicious:
                print(f'  {name}')

            fail('Preparação interrompida por possível segredo.')

        if not copied:
            fail('Nenhum arquivo elegível foi encontrado.')

        published_baseline = False
        published_workspace = False

        try:
            baseline_staging.rename(baseline_destination)
            published_baseline = True

            staging.rename(destination)
            published_workspace = True

            manifest_path = create_manifest(
                repo,
                destination,
                copied,
                skipped,
                baseline_destination,
            )

        except Exception:
            if published_workspace:
                shutil.rmtree(destination)

            if published_baseline:
                shutil.rmtree(baseline_destination)

            raise

    finally:
        if staging.exists():
            shutil.rmtree(staging)

        if baseline_staging.exists():
            shutil.rmtree(baseline_staging)

    print('\nWorkspace preparado com sucesso!')
    print(f'Origem: {repo}')
    print(f'Destino: {destination}')
    print(f'Arquivos copiados: {len(copied)}')
    print(f'Arquivos ignorados: {len(skipped)}')
    print(f'Tamanho copiado: {total_size} bytes')
    print(f'Baseline: {baseline_destination}')

    if skipped:
        print('\nArquivos ignorados:')
        for name, reason in skipped:
            print(f'  {name} ({reason})')

    print('\nO repositório original não foi alterado.')
    print('Revise o conteúdo da cópia antes de executar o Codex.')


def main():
    if len(sys.argv) != 2:
        fail('Uso: prepare_workspace.py <diretorio-git>')

    try:
        prepare(Path(sys.argv[1]))
    except (OSError, subprocess.CalledProcessError) as error:
        fail(str(error))


if __name__ == '__main__':
    main()
