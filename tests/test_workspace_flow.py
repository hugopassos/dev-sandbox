import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / 'bin'


class WorkspaceFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.home = Path(self.temp.name)
        self.repo = self.home / 'example-project'
        self.repo.mkdir()

        self.env = os.environ.copy()
        self.env['HOME'] = str(self.home)

        self.git('init')
        self.write(self.repo / 'main.rb', "puts 'Original'\n")

        self.git('add', 'main.rb')
        self.git(
            '-c', 'user.name=Codex Test',
            '-c', 'user.email=codex-test@example.invalid',
            'commit', '-m', 'Initial commit',
        )

        digest = hashlib.sha256(
            os.fsencode(str(self.repo.resolve()))
        ).hexdigest()[:12]

        self.workspace_name = f'{self.repo.name}-{digest}'

        self.workspace = (
            self.home / '.local/share/dev-sandbox/workspaces'
            / self.workspace_name
        )

        self.manifest = (
            self.home / '.local/share/dev-sandbox/manifests'
            / f'{self.workspace_name}.json'
        )

    def git(self, *arguments):
        return subprocess.run(
            ['git', *arguments],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')

    def command(self, script, *arguments):
        return subprocess.run(
            [
                sys.executable,
                str(BIN / script),
                *map(str, arguments),
            ],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def prepare(self):
        result = self.command(
            'prepare_workspace.py', self.repo
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_prepare_creates_manifest_and_preserves_original(self):
        self.prepare()

        self.assertTrue(self.workspace.is_dir())
        self.assertTrue(self.manifest.is_file())

        manifest = json.loads(self.manifest.read_text())

        self.assertEqual(manifest['schema_version'], 1)
        self.assertEqual(manifest['source'], str(self.repo))
        self.assertEqual(manifest['excluded_files'], [])

        self.assertEqual(
            (self.workspace / 'main.rb').read_text(),
            "puts 'Original'\n",
        )

        self.assertEqual(
            (self.repo / 'main.rb').read_text(),
            "puts 'Original'\n",
        )

        self.assertEqual(
            self.manifest.stat().st_mode & 0o777,
            0o600,
        )

    def test_modified_file_appears_in_review_and_diff(self):
        self.prepare()

        self.write(
            self.workspace / 'main.rb',
            "puts 'Alterado pelo Codex'\n",
        )

        review = self.command('review_workspace.py', self.repo)
        diff = self.command('diff_workspace.py', self.repo)

        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertIn('[MODIFICADO] main.rb', review.stdout)

        self.assertEqual(diff.returncode, 0, diff.stderr)
        self.assertIn("Alterado pelo Codex", diff.stdout)

        self.assertEqual(
            (self.repo / 'main.rb').read_text(),
            "puts 'Original'\n",
        )

    def test_conflict_blocks_diff_for_modified_file(self):
        self.prepare()

        self.write(
            self.workspace / 'main.rb',
            "puts 'Alteração Codex'\n",
        )
        self.write(
            self.repo / 'main.rb',
            "puts 'Alteração desenvolvedor'\n",
        )

        review = self.command('review_workspace.py', self.repo)
        diff = self.command('diff_workspace.py', self.repo)

        self.assertEqual(review.returncode, 2)
        self.assertIn('[CONFLITO] main.rb', review.stdout)

        self.assertEqual(diff.returncode, 2)
        self.assertIn('conflito no original', diff.stdout)
        self.assertNotIn('Alteração Codex', diff.stdout)

    def test_symlink_is_blocked(self):
        self.prepare()

        (self.workspace / 'external-link').symlink_to('/etc/passwd')

        review = self.command('review_workspace.py', self.repo)
        diff = self.command('diff_workspace.py', self.repo)

        self.assertEqual(review.returncode, 2)
        self.assertIn('[INSEGURO] external-link', review.stdout)

        self.assertEqual(diff.returncode, 2)
        self.assertNotIn('root:x:', diff.stdout)

    def test_secret_is_not_displayed_in_diff(self):
        self.prepare()

        fake_secret = 'EXAMPLE_FAKE_SECRET_123456'
        self.write(
            self.workspace / 'settings.py',
            f'api_key = "{fake_secret}"\n',
        )

        review = self.command('review_workspace.py', self.repo)
        diff = self.command('diff_workspace.py', self.repo)

        self.assertEqual(review.returncode, 2)
        self.assertIn('[INSEGURO] settings.py', review.stdout)

        self.assertEqual(diff.returncode, 2)
        self.assertNotIn(fake_secret, diff.stdout)

    def test_original_only_change_is_not_a_conflict(self):
        self.prepare()

        self.write(
            self.repo / 'main.rb',
            "puts 'Alteração somente no original'\n",
        )

        review = self.command('review_workspace.py', self.repo)

        self.assertEqual(review.returncode, 0)
        self.assertIn(
            '[ORIGINAL ALTERADO] main.rb',
            review.stdout,
        )

    def test_prepare_does_not_overwrite_existing_workspace(self):
        self.prepare()

        second = self.command(
            'prepare_workspace.py', self.repo
        )

        self.assertNotEqual(second.returncode, 0)

        self.assertEqual(
            (self.workspace / 'main.rb').read_text(),
            "puts 'Original'\n",
        )

    def test_prepare_applies_root_relative_ignore_rules(self):
        excluded = [
            'notes.txt', 'docs/a.txt', 'docs/nested/b.txt',
            'root.log', 'logs/root.txt', 'logs/a/b/deep.txt',
        ]
        kept = ['nested/notes.txt', 'nested/root.log', 'nested/docs/a.txt']
        for name in excluded + kept + ['.env']:
            self.write(self.repo / name, 'Example\n')
        self.write(
            self.repo / '.dev-sandboxignore',
            '# Comentário\n\n  # Outro comentário\n'
            '/notes.txt\ndocs/\n*.log\nlogs/**/*.txt\n.env\n',
        )
        self.git('add', '-f', '--', *excluded, *kept, '.env', '.dev-sandboxignore')

        result = self.prepare()

        self.assertIn('Arquivos ignorados: 8', result.stdout)
        self.assertIn('.env (nome excluído)', result.stdout)
        manifest = json.loads(self.manifest.read_text())
        self.assertCountEqual(manifest['excluded_files'], [
            *excluded, '.env', '.dev-sandboxignore',
        ])
        baseline = Path(manifest['baseline'])
        for name in manifest['excluded_files']:
            self.assertFalse((self.workspace / name).exists(), name)
            self.assertFalse((baseline / name).exists(), name)
        for name in kept:
            self.assertTrue((self.workspace / name).is_file(), name)

        for script in ('audit', 'review', 'merge'):
            result = self.command(f'{script}_workspace.py', self.repo)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_changed_rules_block_audit_until_workspace_is_replaced(self):
        self.write(self.repo / 'notes.txt', 'Example\n')
        self.git('add', 'notes.txt')
        self.prepare()
        self.write(self.repo / '.dev-sandboxignore', 'notes.txt\n')

        second = self.command('prepare_workspace.py', self.repo)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('workspace já existe', second.stderr)
        self.assertTrue((self.workspace / 'notes.txt').is_file())
        audit = self.command('audit_workspace.py', self.repo)
        self.assertNotEqual(audit.returncode, 0)
        self.assertIn('notes.txt: excluído por .dev-sandboxignore', audit.stderr)

        baseline = Path(json.loads(self.manifest.read_text())['baseline'])
        shutil.rmtree(self.workspace)
        shutil.rmtree(baseline)
        self.manifest.unlink()
        self.prepare()
        self.assertFalse((self.workspace / 'notes.txt').exists())
        audit = self.command('audit_workspace.py', self.repo)
        self.assertEqual(audit.returncode, 0, audit.stderr)

    def test_invalid_rules_do_not_publish_or_modify_workspace(self):
        rules = self.repo / '.dev-sandboxignore'
        self.write(rules, 'docs/\n!main.rb\n')
        result = self.command('prepare_workspace.py', self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('.dev-sandboxignore: regra inválida na linha 2', result.stderr)
        self.assertFalse(self.workspace.exists())
        self.assertFalse(self.manifest.exists())

        rules.unlink()
        self.prepare()
        original_manifest = self.manifest.read_bytes()
        baseline = Path(json.loads(original_manifest)['baseline'])
        self.write(self.workspace / 'main.rb', 'Workspace edit\n')
        self.write(rules, '../main.rb\n')
        result = self.command('prepare_workspace.py', self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('.dev-sandboxignore: regra inválida na linha 1', result.stderr)
        self.assertEqual(self.manifest.read_bytes(), original_manifest)
        self.assertEqual((baseline / 'main.rb').read_text(), "puts 'Original'\n")
        self.assertEqual((self.workspace / 'main.rb').read_text(), 'Workspace edit\n')
        audit = self.command('audit_workspace.py', self.repo)
        self.assertNotEqual(audit.returncode, 0)
        self.assertIn('.dev-sandboxignore', audit.stderr)

    def test_ignore_file_symlink_is_rejected(self):
        (self.repo / '.dev-sandboxignore').symlink_to('absent')
        result = self.command('prepare_workspace.py', self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('.dev-sandboxignore: falha na leitura', result.stderr)
        self.assertFalse(self.workspace.exists())

    def test_ignore_rule_does_not_bypass_content_scan_for_allowed_file(self):
        self.write(self.repo / 'main.rb', 'api_key = "FAKE_EXAMPLE_123456"\n')
        self.write(self.repo / '.dev-sandboxignore', 'other.rb\n')
        result = self.command('prepare_workspace.py', self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Preparação interrompida por possível segredo', result.stderr)
        self.assertFalse(self.workspace.exists())

    def test_prepare_skips_content_scan_for_explicitly_ignored_file(self):
        self.write(self.repo / 'settings.rb', 'api_key = "FAKE_EXAMPLE_123456"\n')
        self.git('add', 'settings.rb')
        self.write(self.repo / '.dev-sandboxignore', 'settings.rb\n')

        result = self.prepare()

        self.assertIn('settings.rb (.dev-sandboxignore)', result.stdout)
        main_size = (self.repo / 'main.rb').stat().st_size
        self.assertIn(f'Tamanho copiado: {main_size} bytes\n', result.stdout)
        self.assertTrue((self.workspace / 'main.rb').is_file())
        self.assertFalse((self.workspace / 'settings.rb').exists())

    def test_review_diff_and_merge_skip_currently_ignored_changes(self):
        self.write(self.repo / 'notes.txt', 'Original notes\n')
        self.git('add', 'notes.txt')
        self.prepare()
        self.write(self.workspace / 'main.rb', 'Allowed edit\n')
        self.write(self.workspace / 'notes.txt', 'Ignored edit\n')
        self.write(self.repo / '.dev-sandboxignore', 'notes.txt\n')

        for script in ('review', 'diff', 'merge'):
            with self.subTest(script=script):
                result = self.command(f'{script}_workspace.py', self.repo)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('main.rb', result.stdout)
                self.assertNotIn('notes.txt', result.stdout)
                self.assertNotIn('Ignored edit', result.stdout)
                if script == 'diff':
                    self.assertIn('+Allowed edit', result.stdout)

        merges = self.workspace.parent.parent / 'merges' / self.workspace_name
        session, = merges.iterdir()
        self.assertEqual((session / 'files' / 'main.rb').read_text(), 'Allowed edit\n')
        self.assertFalse((session / 'files' / 'notes.txt').exists())
        report = json.loads((session / 'merge-report.json').read_text())
        self.assertEqual([record['path'] for record in report['files']], ['main.rb'])

    def test_invalid_or_unreadable_rules_block_review_diff_and_merge(self):
        self.prepare()
        self.write(self.workspace / 'main.rb', 'Allowed edit\n')
        rules = self.repo / '.dev-sandboxignore'
        merges = self.workspace.parent.parent / 'merges'

        for content, expected in (
            (b'other.rb\n!main.rb\n', 'regra inválida na linha 2'),
            (b'\xff', 'falha na leitura'),
        ):
            rules.write_bytes(content)
            for script in ('review', 'diff', 'merge'):
                with self.subTest(script=script, error=expected):
                    result = self.command(f'{script}_workspace.py', self.repo)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(f'.dev-sandboxignore: {expected}', result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertFalse(merges.exists())

    def test_prepare_preserves_immutable_baseline(self):
        self.write(
            self.repo / 'main.rb',
            "puts 'Alteração local antes do prepare'\n",
        )

        self.prepare()

        baseline = (
            self.home / '.local/share/dev-sandbox/baselines'
            / self.workspace_name
        )

        original_content = (
            "puts 'Alteração local antes do prepare'\n"
        )

        self.assertEqual(
            (baseline / 'main.rb').read_text(),
            original_content,
        )

        self.assertEqual(
            (self.workspace / 'main.rb').read_text(),
            original_content,
        )

        self.write(
            self.workspace / 'main.rb',
            "puts 'Alteração do Codex'\n",
        )

        self.assertEqual(
            (baseline / 'main.rb').read_text(),
            original_content,
        )

        manifest = json.loads(self.manifest.read_text())

        self.assertEqual(
            manifest['baseline'],
            str(baseline),
        )

        expected_hash = hashlib.sha256(
            original_content.encode('utf-8')
        ).hexdigest()

        self.assertEqual(
            manifest['files']['main.rb']['sha256'],
            expected_hash,
        )


if __name__ == '__main__':
    unittest.main()
