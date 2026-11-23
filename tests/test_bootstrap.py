import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import PROGRAM_FILES, stage_program


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.program = self.root / 'program'
        self.data = self.root / 'data'
        (self.source / 'web').mkdir(parents=True)
        for name in PROGRAM_FILES:
            (self.source / name).write_text('payload:' + name)
        self.owners = []

    def tearDown(self):
        self.temp.cleanup()

    def stage(self):
        stage_program(self.source, self.program, self.data,
                      chown=lambda path, uid, gid: self.owners.append((Path(path), uid, gid)))

    def test_stages_readable_program_without_editing_source_or_existing_config(self):
        self.data.mkdir()
        (self.data / 'config.json').write_text('existing-private-config')
        before = {n: (self.source / n).read_bytes() for n in PROGRAM_FILES}
        self.stage()
        for name in PROGRAM_FILES:
            self.assertEqual((self.program / name).read_bytes(), before[name])
            self.assertEqual((self.source / name).read_bytes(), before[name])
        self.assertEqual((self.data / 'config.json').read_text(), 'existing-private-config')
        self.assertTrue(all(uid == gid == 10001 for _, uid, gid in self.owners))
        self.assertFalse(any(self.source in path.parents for path, _, _ in self.owners))
        self.assertEqual(list(self.program.rglob('*.new')), [])
        if os.name != 'nt':
            self.assertEqual((self.program / 'app.py').stat().st_mode & 0o777, 0o644)
            self.assertEqual((self.program / 'web').stat().st_mode & 0o777, 0o755)

    def test_missing_source_fails_before_modifying_old_program(self):
        self.stage()
        old = (self.program / 'app.py').read_bytes()
        (self.source / 'app.py').write_text('changed')
        (self.source / 'web/app.js').unlink()
        with self.assertRaises(FileNotFoundError):
            self.stage()
        self.assertEqual((self.program / 'app.py').read_bytes(), old)

    def test_restage_updates_program(self):
        self.stage()
        (self.source / 'web/app.js').write_text('updated')
        self.stage()
        self.assertEqual((self.program / 'web/app.js').read_text(), 'updated')
