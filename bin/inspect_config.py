#!/usr/bin/env python3

import json
import os
import sys


def inspect_config(config):
    service = config['services']['codex']
    proxy = config['services']['proxy']

    mode = os.environ['CODEX_MODE']
    workspace = os.environ['CODEX_WORKSPACE']
    stack = os.environ['CODEX_STACK']
    runtime = os.environ['CODEX_RUNTIME']

    mounts = [
        item
        for item in service.get('volumes', [])
        if item.get('target') == '/workspace'
    ]

    if len(mounts) != 1:
        print('FALHA: montagem do workspace inválida')
        return False

    mount = mounts[0]

    checks = {
        'Nome do ambiente correto':
            config['name'] == stack,

        'Workspace correto':
            mount.get('source') == workspace
            and mount.get('type') == 'bind',

        'Imagem correta':
            service.get('image') == f'codex-isolated:{runtime}',

        'Build target correto':
            service.get('build', {}).get('target') == f'runtime-{runtime}',

        'Permissão do workspace':
            mount.get('read_only', False) == (mode == 'readonly'),

        'Filesystem principal somente leitura':
            service.get('read_only') is True,

        'Capacidades removidas':
            'ALL' in service.get('cap_drop', []),

        'Sem privilégios adicionais':
            'no-new-privileges:true'
            in service.get('security_opt', []),

        'Rede interna':
            config['networks']['codex_internal'].get('internal') is True,

        'Codex apenas na rede interna':
            set(service.get('networks', {})) == {'codex_internal'},

        'Proxy com rede externa separada':
            {'codex_internal', 'proxy_egress'}.issubset(
                set(proxy.get('networks', {}))
            ),
    }

    print('\nConfiguração efetiva:')
    print(f'  Modo: {mode}')
    print(f'  Workspace: {workspace}')
    print(f'  Runtime: {runtime}')
    print(f'  Ambiente: {config["name"]}')
    print(f'  Workspace gravável: {mode == "dev"}')
    print()

    for label, passed in checks.items():
        status = 'OK' if passed else 'FALHA'
        print(f'  {label}: {status}')

    return all(checks.values())


def main():
    try:
        config = json.load(sys.stdin)
        valid = inspect_config(config)
    except (KeyError, ValueError, TypeError) as error:
        print(f'FALHA ao inspecionar configuração: {error}', file=sys.stderr)
        sys.exit(1)

    sys.exit(0 if valid else 1)


if __name__ == '__main__':
    main()
