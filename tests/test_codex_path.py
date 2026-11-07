import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_quota import find_codex


class CodexPathTests(unittest.TestCase):
    def test_explicit_override_beats_path(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'custom-codex'
            binary.touch(mode=0o700)
            with patch.dict(os.environ, {'CODEX_BIN': str(binary)}):
                self.assertEqual(find_codex(), str(binary.resolve()))

    def test_invalid_override_is_not_silently_ignored(self):
        with patch.dict(os.environ, {'CODEX_BIN': '/missing/codex'}):
            with self.assertRaisesRegex(RuntimeError, 'CODEX_BIN'):
                find_codex()

    def test_path_lookup(self):
        with patch.dict(os.environ, {}, clear=True), patch('codex_quota.shutil.which', return_value='/opt/bin/codex'):
            self.assertEqual(find_codex(), '/opt/bin/codex')

    def test_macos_bundle_fallback_when_path_is_empty(self):
        expected = '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
        with patch.dict(os.environ, {}, clear=True), patch('codex_quota.shutil.which', return_value=None), \
             patch('codex_quota.Path.is_file', autospec=True, side_effect=lambda p: str(p) == expected), \
             patch('codex_quota.os.access', return_value=True):
            self.assertEqual(find_codex(), expected)

    def test_no_installation(self):
        with patch.dict(os.environ, {}, clear=True), patch('codex_quota.shutil.which', return_value=None), \
             patch('codex_quota.Path.is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Codex CLI not found'):
                find_codex()
