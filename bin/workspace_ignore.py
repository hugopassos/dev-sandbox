from fnmatch import fnmatchcase

from safe_files import UnsafeFileError, read_regular_file


IGNORE_FILE = '.dev-sandboxignore'
MAX_IGNORE_SIZE = 64 * 1024
LITERAL_GLOB_CHARACTERS = {'?': '[?]', '[': '[[]'}


def load_ignore_rules(repo):
    try:
        content = read_regular_file(
            repo, IGNORE_FILE, MAX_IGNORE_SIZE, require_single_link=True
        ).decode('utf-8')
    except FileNotFoundError:
        return ()
    except (UnicodeError, UnsafeFileError) as error:
        raise ValueError(f'{IGNORE_FILE}: falha na leitura: {error}') from error

    rules = []
    for number, line in enumerate(content.split('\n'), start=1):
        pattern = line.strip(' \t\r')
        if not pattern or pattern.startswith('#'):
            continue

        directories_only = pattern.endswith('/')
        parts = pattern.removeprefix('/').removesuffix('/').split('/')
        if (
            pattern.startswith('!')
            or any(ord(char) < 32 or char == '\\' for char in pattern)
            or any(part in ('', '.', '..') for part in parts)
            or any('**' in part and part != '**' for part in parts)
        ):
            raise ValueError(f'{IGNORE_FILE}: regra inválida na linha {number}.')

        # Apenas * é especial dentro de um componente.
        rules.append((tuple(
            ''.join(LITERAL_GLOB_CHARACTERS.get(char, char) for char in part)
            for part in parts
        ), directories_only))

    return tuple(rules)


def ignored(path, rules):
    parts = path.parts
    for pattern, directories_only in rules:
        # Posições alcançáveis após cada componente do padrão.
        positions = {0}
        for component in pattern:
            if component == '**':
                positions = set(range(min(positions), len(parts) + 1))
            else:
                positions = {
                    index + 1 for index in positions
                    if index < len(parts)
                    and fnmatchcase(parts[index], component)
                }
            if not positions:
                break

        limit = len(parts) - directories_only
        if any(0 < index <= limit for index in positions):
            return True

    return False
