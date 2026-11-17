import tempfile
import unittest
from pathlib import Path

from safe_files import read_regular_file, UnsafeFileError


class SafeFilesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_regular_file(self):
        (self.root / 'example.txt').write_text('hello')

        result = read_regular_file(
            self.root, 'example.txt', 1024
        )

        self.assertEqual(result, b'hello')

    def test_rejects_symlink(self):
        (self.root / 'link').symlink_to('/etc/passwd')

        with self.assertRaises(UnsafeFileError):
            read_regular_file(self.root, 'link', 1024)

    def test_rejects_symlink_directory(self):
        (self.root / 'external').symlink_to('/etc')

        with self.assertRaises(UnsafeFileError):
            read_regular_file(
                self.root, 'external/passwd', 1024
            )

    def test_rejects_path_traversal(self):
        with self.assertRaises(UnsafeFileError):
            read_regular_file(
                self.root, '../example.txt', 1024
            )

    def test_rejects_oversized_file(self):
        (self.root / 'large.txt').write_bytes(b'x' * 2048)

        with self.assertRaises(UnsafeFileError):
            read_regular_file(
                self.root, 'large.txt', 1024
            )

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            read_regular_file(
                self.root, 'missing.txt', 1024
            )


if __name__ == '__main__':
    unittest.main()
