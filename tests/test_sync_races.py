import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))

import sync_workspace


class SyncRacesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.repo = Path(self.temp.name) / 'repository'
        self.repo.mkdir()

    def write(self, name, content):
        path = self.repo / name
        path.write_text(content, encoding='utf-8')
        return path

    def digest(self, content):
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def operation(self, action, name, baseline, content=None):
        payload = content.encode('utf-8') if content is not None else None

        return {
            'action': action,
            'name': name,
            'baseline': baseline,
            'workspace_hash': (
                hashlib.sha256(payload).hexdigest()
                if payload is not None
                else None
            ),
            'data': payload,
        }

    def test_create_does_not_overwrite_concurrently_created_file(self):
        name = 'new.rb'

        operation = self.operation(
            'CREATE',
            name,
            baseline=None,
            content="puts 'Codex'\n",
        )

        concurrent_content = "puts 'Outro processo'\n"

        original_link = os.link

        def concurrent_create(*args, **kwargs):
            # Outro processo cria o destino imediatamente
            # antes da operação de criação exclusiva.
            self.write(name, concurrent_content)
            return original_link(*args, **kwargs)

        with patch.object(
            sync_workspace.os,
            'link',
            side_effect=concurrent_create,
        ):
            with self.assertRaises(sync_workspace.SyncError):
                sync_workspace.apply_one(self.repo, operation)

        self.assertEqual(
            (self.repo / name).read_text(),
            concurrent_content,
        )

    def test_update_does_not_overwrite_concurrent_modification(self):
        name = 'main.rb'

        initial_content = "puts 'Original'\n"
        concurrent_content = "puts 'Alteração concorrente'\n"

        self.write(name, initial_content)

        operation = self.operation(
            'UPDATE',
            name,
            baseline=self.digest(initial_content),
            content="puts 'Codex'\n",
        )

        original_replace = os.replace

        def concurrent_update(*args, **kwargs):
            # A verificação inicial já aconteceu.
            # Outro processo modifica o mesmo arquivo.
            self.write(name, concurrent_content)
            return original_replace(*args, **kwargs)

        with patch.object(
            sync_workspace.os,
            'replace',
            side_effect=concurrent_update,
        ):
            with self.assertRaises(sync_workspace.SyncError):
                sync_workspace.apply_one(self.repo, operation)

        self.assertEqual(
            (self.repo / name).read_text(),
            concurrent_content,
        )

    def test_delete_does_not_remove_concurrent_modification(self):
        name = 'main.rb'

        initial_content = "puts 'Original'\n"
        concurrent_content = "puts 'Alteração concorrente'\n"

        self.write(name, initial_content)

        operation = self.operation(
            'DELETE',
            name,
            baseline=self.digest(initial_content),
        )

        original_unlink = os.unlink

        def concurrent_delete(filename, *args, **kwargs):
            if filename == name:
                # O arquivo é alterado imediatamente antes
                # da tentativa de exclusão.
                self.write(name, concurrent_content)

            return original_unlink(filename, *args, **kwargs)

        with patch.object(
            sync_workspace.os,
            'unlink',
            side_effect=concurrent_delete,
        ):
            with self.assertRaises(sync_workspace.SyncError):
                sync_workspace.apply_one(self.repo, operation)

        self.assertEqual(
            (self.repo / name).read_text(),
            concurrent_content,
        )


if __name__ == '__main__':
    unittest.main()
