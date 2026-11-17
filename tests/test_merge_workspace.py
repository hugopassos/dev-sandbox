import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / 'bin'


class MergeWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.home = Path(self.temp.name)
        self.repo = self.home / 'test-project'
        self.repo.mkdir()

        self.env = os.environ.copy()
        self.env['HOME'] = str(self.home)

        self.write(
            self.repo / 'calculator.rb',
            self.initial_code(),
        )

        self.git('init')
        self.git('add', 'calculator.rb')
        self.git(
            '-c', 'user.name=Codex Test',
            '-c', 'user.email=codex-test@example.invalid',
            'commit', '-m', 'Initial commit',
        )

        digest = hashlib.sha256(
            os.fsencode(str(self.repo.resolve()))
        ).hexdigest()[:12]

        name = f'{self.repo.name}-{digest}'

        self.storage = (
            self.home / '.local/share/codex-isolated'
        )
        self.workspace = self.storage / 'workspaces' / name
        self.baseline = self.storage / 'baselines' / name
        self.merges_dir = self.storage / 'merges' / name
        self.merged = None

        result = self.command('prepare_workspace.py')
        self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def initial_code():
        return (
            'class Calculator\n'
            '  def sum(a, b)\n'
            '    a + b\n'
            '  end\n'
            '\n'
            '  # Separation between operations\n'
            '\n'
            '  def subtract(a, b)\n'
            '    a - b\n'
            '  end\n'
            'end\n'
        )

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def git(self, *arguments):
        subprocess.run(
            ['git', *arguments],
            cwd=self.repo,
            env=self.env,
            check=True,
            capture_output=True,
            text=True,
        )

    def command(self, script):
        return subprocess.run(
            [
                sys.executable,
                str(BIN / script),
                str(self.repo),
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )
    
    def merge(self):
        previous_sessions = (
            set(self.merges_dir.glob('session-*'))
            if self.merges_dir.exists()
            else set()
        )

        result = self.command('merge_workspace.py')

        current_sessions = (
            set(self.merges_dir.glob('session-*'))
            if self.merges_dir.exists()
            else set()
        )

        new_sessions = current_sessions - previous_sessions

        if result.returncode == 1:
            self.assertFalse(
                new_sessions,
                f'Merge falhou, mas deixou sessões novas:\n'
                f'{new_sessions}\n'
                f'STDERR:\n{result.stderr}'
            )

            return result, None

        self.assertIn(
            result.returncode,
            (0, 2),
            f'Código de saída inesperado.\n'
            f'STDOUT:\n{result.stdout}\n'
            f'STDERR:\n{result.stderr}'
        )

        self.assertEqual(
            len(new_sessions),
            1,
            f'Era esperada exatamente uma sessão nova.\n'
            f'Encontradas: {new_sessions}\n'
            f'STDOUT:\n{result.stdout}\n'
            f'STDERR:\n{result.stderr}'
        )

        self.merged = new_sessions.pop()

        report_path = self.merged / 'merge-report.json'

        self.assertTrue(
            report_path.is_file(),
            f'Relatório não encontrado: {report_path}'
        )

        return result, json.loads(report_path.read_text())

    def test_ready_when_only_codex_changes_file(self):
        self.write(
            self.workspace / 'calculator.rb',
            self.initial_code().replace('a + b', 'a + b + 1'),
        )

        result, report = self.merge()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['files'][0]['status'], 'READY')

        self.assertIn(
            'a + b + 1',
            (self.merged / 'files' / 'calculator.rb').read_text(),
        )

        self.assertEqual(
            (self.repo / 'calculator.rb').read_text(),
            self.initial_code(),
        )

    def test_merges_independent_changes(self):
        self.write(
            self.repo / 'calculator.rb',
            self.initial_code().replace('a + b', 'a + b + 1'),
        )

        self.write(
            self.workspace / 'calculator.rb',
            self.initial_code().replace('a - b', 'a - b - 1'),
        )

        result, report = self.merge()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['files'][0]['status'], 'MERGED')

        content = (self.merged / 'files' / 'calculator.rb').read_text()

        self.assertIn('a + b + 1', content)
        self.assertIn('a - b - 1', content)

    def test_conflict_when_both_change_same_line(self):
        self.write(
            self.repo / 'calculator.rb',
            self.initial_code().replace('a + b', 'a + b + 1'),
        )

        self.write(
            self.workspace / 'calculator.rb',
            self.initial_code().replace('a + b', 'a + b + 2'),
        )

        result, report = self.merge()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(report['files'][0]['status'], 'CONFLICT')

        content = (self.merged / 'files' / 'calculator.rb').read_text()

        self.assertIn('<<<<<<< ORIGINAL', content)
        self.assertIn('=======', content)
        self.assertIn('>>>>>>> CODEX', content)

    def test_create_new_file(self):
        self.write(
            self.workspace / 'new.rb',
            "puts 'Criado pelo Codex'\n",
        )

        result, report = self.merge()

        self.assertEqual(result.returncode, 0, result.stderr)

        statuses = {
            item['path']: item['status']
            for item in report['files']
        }

        self.assertEqual(statuses['new.rb'], 'CREATE')
        self.assertFalse((self.repo / 'new.rb').exists())
        self.assertTrue((self.merged / 'files' / 'new.rb').exists())

    def test_delete_existing_file(self):
        (self.workspace / 'calculator.rb').unlink()

        result, report = self.merge()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['files'][0]['status'], 'DELETE')
        self.assertFalse((self.merged / 'files' / 'calculator.rb').exists())
        self.assertTrue((self.repo / 'calculator.rb').exists())

    def test_delete_vs_modify_requires_manual_resolution(self):
        (self.workspace / 'calculator.rb').unlink()

        self.write(
            self.repo / 'calculator.rb',
            self.initial_code().replace('a + b', 'a + b + 1'),
        )

        result, report = self.merge()

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(report['files'][0]['status'], 'MANUAL')

        self.assertFalse((self.merged / 'files' / 'calculator.rb').exists())
        self.assertTrue((self.repo / 'calculator.rb').exists())

    def test_modified_baseline_is_rejected(self):
        self.write(
            self.workspace / 'calculator.rb',
            self.initial_code().replace('a + b', 'a + b + 1'),
        )

        self.write(
            self.baseline / 'calculator.rb',
            "puts 'Baseline adulterada'\n",
        )

        result, report = self.merge()

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Hash inconsistente', result.stderr)
        self.assertIsNone(report)
        self.assertIsNone(self.merged)
    
    def test_multiple_merge_sessions_preserve_previous_results(self):
        self.write(
            self.workspace / 'calculator.rb',
            self.initial_code().replace('a + b', 'a + b + 1'),
        )

        first, first_report = self.merge()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first_report['files'][0]['status'], 'READY')

        first_session = self.merged
        first_content = (
            first_session / 'files' / 'calculator.rb'
        ).read_text()

        self.write(
            self.workspace / 'calculator.rb',
            self.initial_code().replace('a + b', 'a + b + 2'),
        )

        second, second_report = self.merge()

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second_report['files'][0]['status'], 'READY')

        second_session = self.merged

        self.assertNotEqual(first_session, second_session)
        self.assertTrue(first_session.is_dir())
        self.assertTrue(second_session.is_dir())

        self.assertEqual(
            (first_session / 'files' / 'calculator.rb').read_text(),
            first_content,
        )

        self.assertIn(
            'a + b + 2',
            (second_session / 'files' / 'calculator.rb').read_text(),
        )

        self.assertEqual(
            (self.repo / 'calculator.rb').read_text(),
            self.initial_code(),
        )


if __name__ == '__main__':
    unittest.main()
