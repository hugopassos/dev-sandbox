#!/usr/bin/env python3

import argparse
import fcntl
import hashlib
import json
import os
import stat
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone
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


BACKUPS_DIR = Path.home() / '.local/share/codex-isolated/backups'


class SyncError(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_checked(root, name, expected):
    try:
        data = read_regular_file(root, name, MAX_FILE_SIZE)
        content = data.decode('utf-8')
    except (OSError, UnsafeFileError, UnicodeError) as error:
        raise SyncError(f'Leitura insegura: {name}') from error

    if '\0' in content:
        raise SyncError(f'Arquivo binário: {name}')

    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        raise SyncError(f'Conteúdo suspeito: {name}')

    if sha256(data) != expected:
        raise SyncError(f'Arquivo mudou durante a operação: {name}')

    return data


def open_parent(root, name):
    """
    Abre o diretório pai usando descritores e O_NOFOLLOW.
    Não cria diretórios novos.
    """
    parts = name.split('/')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    stack = ExitStack()

    try:
        fd = os.open(root, flags)
        stack.callback(os.close, fd)

        for component in parts[:-1]:
            fd = os.open(component, flags, dir_fd=fd)
            stack.callback(os.close, fd)

        return stack, fd, parts[-1]
    except Exception:
        stack.close()
        raise


def inspect_destination(parent_fd, filename):
    try:
        info = os.stat(
            filename,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None

    if not stat.S_ISREG(info.st_mode):
        raise SyncError(f'Destino não é arquivo regular: {filename}')

    return info


def plan(source, workspace, manifest):
    baseline = {
        name: metadata['sha256']
        for name, metadata in manifest['files'].items()
    }

    current, unsafe_paths = workspace_files(workspace)

    if unsafe_paths:
        raise SyncError(
            f'Workspace contém caminhos inseguros: '
            f'{len(unsafe_paths)} encontrado(s)'
        )

    operations = []

    for name in sorted(set(baseline) | current):
        if not valid_relative(name) or excluded(Path(name)):
            raise SyncError(f'Caminho não permitido: {name!r}')

        workspace_state, workspace_hash = inspect_file(
            workspace, name, scan_secrets=True
        )

        source_state, source_hash = inspect_file(
            source, name, scan_secrets=True
        )

        if 'unsafe' in (workspace_state, source_state):
            raise SyncError(f'Arquivo inseguro: {name!r}')

        if name in baseline:
            initial = baseline[name]

            changed = (
                workspace_state == 'missing'
                or workspace_hash != initial
            )

            if not changed:
                continue

            if source_state != 'regular' or source_hash != initial:
                raise SyncError(f'Conflito no original: {name!r}')

            action = (
                'DELETE'
                if workspace_state == 'missing'
                else 'UPDATE'
            )

        else:
            if workspace_state != 'regular':
                raise SyncError(f'Arquivo novo inválido: {name!r}')

            if source_state != 'missing':
                raise SyncError(f'Arquivo já existe no original: {name!r}')

            action = 'CREATE'

        # Valida também os diretórios pais existentes.
        try:
            stack, _, _ = open_parent(source, name)
            stack.close()
        except OSError as error:
            raise SyncError(
                f'Diretório pai ausente ou inseguro: {name!r}'
            ) from error

        payload = None

        if action != 'DELETE':
            payload = read_checked(workspace, name, workspace_hash)

        operations.append({
            'action': action,
            'name': name,
            'baseline': baseline.get(name),
            'workspace_hash': workspace_hash,
            'data': payload,
        })

    return operations


def validate_original(parent_fd, filename, expected):
    info = inspect_destination(parent_fd, filename)

    if expected is None:
        if info is not None:
            raise SyncError(f'Destino criado por outro processo: {filename}')
        return None

    if info is None:
        raise SyncError(f'Destino desapareceu: {filename}')

    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(filename, flags, dir_fd=parent_fd)

    try:
        opened = os.fstat(fd)

        if not stat.S_ISREG(opened.st_mode):
            raise SyncError(f'Destino inseguro: {filename}')

        if opened.st_size > MAX_FILE_SIZE:
            raise SyncError(f'Destino muito grande: {filename}')

        chunks = []
        remaining = MAX_FILE_SIZE + 1

        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)

        data = b''.join(chunks)

        if len(data) > MAX_FILE_SIZE or sha256(data) != expected:
            raise SyncError(f'Destino alterado: {filename}')

        # Confere se o nome continua apontando ao mesmo inode.
        current = inspect_destination(parent_fd, filename)

        if (
            current is None
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino
        ):
            raise SyncError(f'Destino substituído: {filename}')

        return data, stat.S_IMODE(opened.st_mode)
    finally:
        os.close(fd)


def create_backup(source, operations):
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.chmod(0o700)

    backup = Path(tempfile.mkdtemp(
        prefix='sync-',
        dir=BACKUPS_DIR,
    ))
    backup.chmod(0o700)

    records = []

    for index, operation in enumerate(operations):
        record = {
            'action': operation['action'],
            'name': operation['name'],
            'original_sha256': operation['baseline'],
            'backup_file': None,
        }

        if operation['baseline'] is not None:
            data = read_checked(
                source,
                operation['name'],
                operation['baseline'],
            )

            backup_name = f'{index:06d}.bak'
            path = backup / backup_name

            with path.open('xb') as file:
                os.chmod(path, 0o600)
                file.write(data)
                file.flush()
                os.fsync(file.fileno())

            record['backup_file'] = backup_name

        records.append(record)

    metadata = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source': str(source),
        'operations': records,
    }

    metadata_path = backup / 'manifest.json'

    with metadata_path.open('x', encoding='utf-8') as file:
        os.chmod(metadata_path, 0o600)
        json.dump(metadata, file, indent=2, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())

    return backup


def apply_one(source, operation):
    name = operation['name']
    action = operation['action']
    expected = operation['baseline']

    stack, parent_fd, filename = open_parent(source, name)

    with stack:
        original = validate_original(parent_fd, filename, expected)

        if action == 'DELETE':
            os.unlink(filename, dir_fd=parent_fd)
            return

        payload = operation['data']

        if payload is None or sha256(payload) != operation['workspace_hash']:
            raise SyncError(f'Conteúdo do workspace inválido: {name!r}')

        mode = original[1] if original else 0o600

        temporary = (
            f'.codex-sync-{os.getpid()}-'
            f'{os.urandom(8).hex()}.tmp'
        )

        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )

        try:
            os.fchmod(fd, mode)

            with os.fdopen(fd, 'wb', closefd=False) as file:
                file.write(payload)
                file.flush()
                os.fsync(fd)

            # Revalida antes de substituir o nome de destino.
            validate_original(parent_fd, filename, expected)

            if action == 'CREATE':
                # Cria o nome definitivo somente se ainda não existir.
                # O link é criado atomicamente no mesmo filesystem.
                try:
                    os.link(
                        temporary,
                        filename,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as error:
                    raise SyncError(
                        f'Destino criado por outro processo: {name}'
                    ) from error
            else:
                # Ainda não elimina a corrida de UPDATE.
                # Será tratado na próxima etapa.
                validate_original(parent_fd, filename, expected)

                os.replace(
                    temporary,
                    filename,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
        finally:
            os.close(fd)

            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass


def run(source_path, apply):
    source, workspace, manifest = load_manifest(source_path)

    # Impede duas execuções simultâneas desta ferramenta
    # para o mesmo manifesto.
    manifest_path = (
        Path.home()
        / '.local/share/codex-isolated/manifests'
        / f'{workspace.name}.json'
    )

    fd = os.open(manifest_path, os.O_RDONLY | os.O_NOFOLLOW)

    with os.fdopen(fd, 'rb') as locked_manifest:
        fcntl.flock(locked_manifest, fcntl.LOCK_EX)

        operations = plan(source, workspace, manifest)

        print(f'\nOrigem: {source}')
        print(f'Workspace: {workspace}')
        print('\nPlano de sincronização:\n')

        for operation in operations:
            print(f"  [{operation['action']}] {operation['name']!r}")

        print(f'\nTotal: {len(operations)} operação(ões)')

        if not operations:
            print('Nenhuma alteração a aplicar.')
            return 0

        if not apply:
            print('\nDry-run: nenhum arquivo foi modificado.')
            print('A aplicação automática foi descontinuada.')
            return 0

        raise SyncError(
            'O --apply foi desabilitado. '
            'Utilize o futuro comando merge para preparar '
            'uma integração assistida.'
        )

        confirmation = input(
            '\nDigite APLICAR para confirmar as alterações: '
        )

        if confirmation != 'APLICAR':
            print('Operação cancelada.')
            return 1

        # Recalcula o plano após a confirmação.
        refreshed = plan(source, workspace, manifest)

        if refreshed != operations:
            raise SyncError('Arquivos mudaram após a confirmação.')

        backup = create_backup(source, refreshed)

        print(f'\nBackup: {backup}')
        print('Aplicando alterações...')

        applied = 0

        try:
            for operation in refreshed:
                apply_one(source, operation)
                applied += 1
        except Exception:
            print(
                f'\nFALHA: {applied} operação(ões) já aplicada(s).',
                file=sys.stderr,
            )
            print(
                f'Backup preservado em: {backup}',
                file=sys.stderr,
            )
            print(
                'Não execute novamente sem revisar o estado do projeto.',
                file=sys.stderr,
            )
            raise

        print(f'\nSincronização concluída: {applied} operação(ões).')
        print(f'Backup preservado em: {backup}')
        print('O workspace e o manifesto foram mantidos.')
        return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    try:
        return run(args.source, args.apply)
    except (SyncError, OSError, ValueError, EOFError) as error:
        print(f'ERRO: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
