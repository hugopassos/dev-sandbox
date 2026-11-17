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
            self.home / '.local/share/codex-isolated/workspaces'
            / self.workspace_name
        )

        self.manifest = (
            self.home / '.local/share/codex-isolated/manifests'
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
    

    def sync_apply(self, confirmation='APLICAR'):
        return subprocess.run(
            [
                sys.executable,
                str(BIN / 'sync_workspace.py'),
                str(self.repo),
                '--apply',
            ],
            env=self.env,
            input=f'{confirmation}\n',
            capture_output=True,
            text=True,
        )

    def test_sync_dry_run_does_not_modify_original(self):
        self.prepare()

        self.write(
            self.workspace / 'main.rb',
            "puts 'Codex'\n",
        )

        result = self.command('sync_workspace.py', self.repo)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('[UPDATE]', result.stdout)
        self.assertIn('Dry-run', result.stdout)
        self.assertEqual(
            (self.repo / 'main.rb').read_text(),
            "puts 'Original'\n",
        )

    def test_sync_applies_modification_and_creates_backup(self):
        self.prepare()

        self.write(
            self.workspace / 'main.rb',
            "puts 'Codex'\n",
        )

        result = self.sync_apply()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.repo / 'main.rb').read_text(),
            "puts 'Codex'\n",
        )

        backup_root = (
            self.home / '.local/share/codex-isolated/backups'
        )

        backups = list(backup_root.glob('sync-*'))
        self.assertEqual(len(backups), 1)

        self.assertEqual(
            (backups[0] / '000000.bak').read_text(),
            "puts 'Original'\n",
        )

    def test_sync_creates_new_file(self):
        self.prepare()

        self.write(
            self.workspace / 'new.rb',
            "puts 'Novo'\n",
        )

        result = self.sync_apply()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.repo / 'new.rb').read_text(),
            "puts 'Novo'\n",
        )

    def test_sync_deletes_file_and_preserves_backup(self):
        self.prepare()

        (self.workspace / 'main.rb').unlink()

        result = self.sync_apply()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.repo / 'main.rb').exists())

        backup_root = (
            self.home / '.local/share/codex-isolated/backups'
        )

        backup = next(backup_root.glob('sync-*'))

        self.assertEqual(
            (backup / '000000.bak').read_text(),
            "puts 'Original'\n",
        )

    def test_sync_rejects_conflict(self):
        self.prepare()

        self.write(
            self.workspace / 'main.rb',
            "puts 'Codex'\n",
        )
        self.write(
            self.repo / 'main.rb',
            "puts 'Desenvolvedor'\n",
        )

        result = self.sync_apply()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Conflito', result.stderr)

        self.assertEqual(
            (self.repo / 'main.rb').read_text(),
            "puts 'Desenvolvedor'\n",
        )

    def test_sync_requires_explicit_confirmation(self):
        self.prepare()

        self.write(
            self.workspace / 'main.rb',
            "puts 'Codex'\n",
        )

        result = self.sync_apply(confirmation='NAO')

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            (self.repo / 'main.rb').read_text(),
            "puts 'Original'\n",
        )

    def test_sync_rejects_symlink_destination(self):
        self.prepare()

        self.write(
            self.workspace / 'new.rb',
            "puts 'Novo'\n",
        )

        (self.repo / 'new.rb').symlink_to('/etc/passwd')

        result = self.sync_apply()

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.repo / 'new.rb').is_symlink())

    def test_prepare_preserves_immutable_baseline(self):
        self.write(
            self.repo / 'main.rb',
            "puts 'Alteração local antes do prepare'\n",
        )

        self.prepare()

        baseline = (
            self.home / '.local/share/codex-isolated/baselines'
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
