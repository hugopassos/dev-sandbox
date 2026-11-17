import os
import stat
from contextlib import ExitStack


class UnsafeFileError(Exception):
    pass


def read_regular_file(root, relative, max_size):
    """
    Lê um arquivo regular dentro de root, sem seguir
    links simbólicos nos componentes do caminho.

    Utiliza descritores de arquivo para impedir que
    a troca de um componente redirecione a leitura.
    """

    if not isinstance(relative, str) or not relative:
        raise UnsafeFileError('Caminho inválido')

    parts = relative.split('/')

    if (
        relative.startswith('/')
        or '\\' in relative
        or '\0' in relative
        or any(part in ('', '.', '..') for part in parts)
    ):
        raise UnsafeFileError('Caminho inválido')

    directory_flags = (
        os.O_RDONLY |
        os.O_DIRECTORY |
        os.O_NOFOLLOW
    )

    file_flags = os.O_RDONLY | os.O_NOFOLLOW

    if hasattr(os, 'O_CLOEXEC'):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC

    with ExitStack() as stack:
        try:
            directory_fd = os.open(root, directory_flags)
            stack.callback(os.close, directory_fd)

            for component in parts[:-1]:
                directory_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=directory_fd,
                )
                stack.callback(os.close, directory_fd)

            file_fd = os.open(
                parts[-1],
                file_flags,
                dir_fd=directory_fd,
            )
            stack.callback(os.close, file_fd)

            info = os.fstat(file_fd)

            if not stat.S_ISREG(info.st_mode):
                raise UnsafeFileError('Arquivo não regular')

            if info.st_size > max_size:
                raise UnsafeFileError('Arquivo muito grande')

            chunks = []
            remaining = max_size + 1

            while remaining > 0:
                chunk = os.read(file_fd, min(65536, remaining))

                if not chunk:
                    break

                chunks.append(chunk)
                remaining -= len(chunk)

            data = b''.join(chunks)

            if len(data) > max_size:
                raise UnsafeFileError('Limite de leitura excedido')

            return data

        except FileNotFoundError:
            raise
        except OSError as error:
            raise UnsafeFileError(
                f'Não foi possível ler o arquivo: {error.strerror}'
            ) from error
