import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / 'bin/dev-sandbox'
PREPARE = ROOT / 'bin/prepare_workspace.py'


FAKE_DOCKER = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

log = Path(os.environ['DOCKER_TEST_LOG'])

with log.open('a', encoding='utf-8') as file:
    file.write(json.dumps({
        'args': sys.argv[1:],
        'workspace': os.environ.get('DEV_SANDBOX_WORKSPACE'),
        'stack': os.environ.get('DEV_SANDBOX_STACK'),
        'runtime': os.environ.get('DEV_SANDBOX_RUNTIME'),
    }) + '\n')

if 'config' not in sys.argv:
    sys.exit('Comando Docker inesperado')

if '--format' in sys.argv:
    runtime = os.environ['DEV_SANDBOX_RUNTIME']
    mode = 'dev' if 'compose.dev.yaml' in sys.argv else 'readonly'

    config = {
        'name': os.environ['DEV_SANDBOX_STACK'],
        'services': {
            'codex': {
                'image': f'dev-sandbox:{runtime}',
                'build': {'target': f'runtime-{runtime}'},
                'read_only': True,
                'cap_drop': ['ALL'],
                'security_opt': ['no-new-privileges:true'],
                'networks': {'codex_internal': None},
                'volumes': [{
                    'type': 'bind',
                    'source': os.environ['DEV_SANDBOX_WORKSPACE'],
                    'target': '/workspace',
                    'read_only': mode == 'readonly',
                }],
            },
            'proxy': {
                'networks': {
                    'codex_internal': None,
                    'proxy_egress': None,
                },
            },
        },
        'networks': {
            'codex_internal': {'internal': True},
            'proxy_egress': {'internal': False},
        },
    }

    print(json.dumps(config))
'''


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.home = Path(self.temp.name)
        self.repo = self.home / 'example-project'
        self.repo.mkdir()

        self.fake_bin = self.home / 'fake-bin'
        self.fake_bin.mkdir()

        docker = self.fake_bin / 'docker'
        docker.write_text(FAKE_DOCKER, encoding='utf-8')
        docker.chmod(0o755)

        self.log = self.home / 'docker-calls.jsonl'

        self.env = os.environ.copy()
        self.env['HOME'] = str(self.home)
        self.env['PATH'] = (
            f'{self.fake_bin}:{self.env["PATH"]}'
        )
        self.env['DOCKER_TEST_LOG'] = str(self.log)

        self.git(self.repo, 'init')

        (self.repo / 'main.rb').write_text(
            "puts 'Teste'\n",
            encoding='utf-8',
        )

        self.git(self.repo, 'add', 'main.rb')
        self.git(
            self.repo,
            '-c', 'user.name=Codex Test',
            '-c', 'user.email=codex-test@example.invalid',
            'commit', '-m', 'Initial commit',
        )

        digest = hashlib.sha256(
            os.fsencode(str(self.repo.resolve()))
        ).hexdigest()[:12]

        self.workspace = (
            self.home
            / '.local/share/dev-sandbox/workspaces'
            / f'{self.repo.name}-{digest}'
        )

    def git(self, directory, *arguments):
        subprocess.run(
            ['git', '-C', str(directory), *arguments],
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )

    def prepare(self):
        result = subprocess.run(
            [sys.executable, str(PREPARE), str(self.repo)],
            env=self.env,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            result.returncode, 0,
            result.stdout + result.stderr,
        )

    def launcher(self, *arguments):
        return subprocess.run(
            ['bash', str(LAUNCHER), *map(str, arguments)],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def docker_calls(self):
        if not self.log.exists():
            return []

        return [
            json.loads(line)
            for line in self.log.read_text().splitlines()
        ]

    def assert_workspace_isolated(self, mode):
        self.prepare()

        result = self.launcher(
            'status', mode, self.repo, 'ruby'
        )

        self.assertEqual(
            result.returncode, 0,
            result.stdout + result.stderr,
        )

        self.assertIn(
            f'Projeto original: {self.repo}',
            result.stdout,
        )

        self.assertIn(
            f'Workspace isolado: {self.workspace}',
            result.stdout,
        )

        calls = self.docker_calls()

        self.assertTrue(calls)

        for call in calls:
            self.assertEqual(
                call['workspace'],
                str(self.workspace),
            )
            self.assertNotEqual(
                call['workspace'],
                str(self.repo),
            )

        self.assertIn(
            'Permissão do workspace: OK',
            result.stdout,
        )

    def test_dev_uses_prepared_workspace(self):
        self.assert_workspace_isolated('dev')

    def test_readonly_uses_prepared_workspace(self):
        self.assert_workspace_isolated('readonly')

    def test_unprepared_git_repository_is_rejected(self):
        result = self.launcher(
            'status', 'dev', self.repo, 'ruby'
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.docker_calls(), [])

    def test_non_git_directory_is_rejected(self):
        directory = self.home / 'not-a-repository'
        directory.mkdir()

        result = self.launcher(
            'status', 'dev', directory, 'ruby'
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.docker_calls(), [])

    def test_prepared_workspace_cannot_be_passed_directly(self):
        self.prepare()

        result = self.launcher(
            'status', 'dev', self.workspace, 'ruby'
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.docker_calls(), [])

    
    def test_sensitive_content_blocks_docker_execution(self):
        self.prepare()

        (self.workspace / 'suspicious.py').write_text(
            'api_key = "EXAMPLE_FAKE_SECRET_123456"\n',
            encoding='utf-8',
        )

        result = self.launcher(
            'status', 'dev', self.repo, 'ruby'
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            'suspicious.py',
            result.stdout + result.stderr,
        )
        self.assertEqual(self.docker_calls(), [])

    def test_symlink_blocks_docker_execution(self):
        self.prepare()

        (self.workspace / 'external-link').symlink_to(
            '/etc/passwd'
        )

        result = self.launcher(
            'status', 'dev', self.repo, 'ruby'
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            'external-link',
            result.stdout + result.stderr,
        )
        self.assertEqual(self.docker_calls(), [])

    def test_clean_workspace_passes_audit(self):
        self.prepare()

        result = self.launcher(
            'status', 'dev', self.repo, 'ruby'
        )

        self.assertEqual(
            result.returncode,
            0,
            result.stdout + result.stderr,
        )

        self.assertIn(
            'Auditoria do workspace: OK',
            result.stdout,
        )

        self.assertTrue(self.docker_calls())

    def test_hard_link_blocks_docker_execution(self):
        self.prepare()

        external_file = self.home / 'external-data.txt'
        external_file.write_text(
            'Conteúdo externo de teste\n',
            encoding='utf-8',
        )

        hard_link = self.workspace / 'external-hardlink.txt'

        os.link(external_file, hard_link)

        result = self.launcher(
            'status', 'dev', self.repo, 'ruby'
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            'external-hardlink.txt',
            result.stdout + result.stderr,
        )
        self.assertEqual(self.docker_calls(), [])


if __name__ == '__main__':
    unittest.main()
