import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from harness.core import Manager, Store
from harness.app import create_demo


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = Manager(Store(self.temp.name), codex='missing-test-cli')
        self.sample = create_demo(self.manager)

    def test_missing_cli_and_clean_repo_are_distinguished(self):
        with patch('harness.core.shutil.which', return_value=None):
            report = self.manager.preflight(self.sample['project']['id'])
        self.assertFalse(report['ready'])
        self.assertEqual(report['checks'][0]['status'], 'pass')
        self.assertEqual(report['checks'][1]['status'], 'fail')
        self.assertIsNone(self.manager.active)
        self.assertEqual(self.manager.store.state()['runs'], [])

    def test_dirty_repository_and_missing_test_program(self):
        project = self.sample['project']
        Path(project['path'], 'new.txt').write_text('dirty')
        with patch('harness.core.shutil.which', return_value=None):
            report = self.manager.preflight(project['id'])
        self.assertEqual(report['checks'][0]['status'], 'fail')

    def test_login_output_is_not_exposed(self):
        with patch('harness.core.shutil.which', return_value='codex'), patch('harness.core.git', return_value=''), patch('harness.core.subprocess.run', return_value=subprocess.CompletedProcess([],0,b'sensitive',b'sensitive')) as run:
            report = self.manager.preflight(self.sample['project']['id'])
        self.assertTrue(report['ready'])
        self.assertNotIn('sensitive', str(report))
        self.assertEqual(run.call_args.args[0], ['codex','login','status'])
        self.assertTrue(any(c['status']=='unknown' for c in report['checks']))
