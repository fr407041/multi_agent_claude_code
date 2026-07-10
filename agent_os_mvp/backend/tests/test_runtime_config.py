from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.ai_company_monitor import REPO_ROOT, get_results_root


class RuntimeConfigTests(unittest.TestCase):
    def test_default_results_root(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_results_root(), REPO_ROOT / "results" / "ai_company_task_harness")

    def test_absolute_results_root_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"AI_COMPANY_RESULTS_ROOT": temp_dir}, clear=True):
                self.assertEqual(get_results_root(), Path(temp_dir).resolve())

    def test_relative_results_root_is_project_relative(self) -> None:
        with patch.dict(os.environ, {"AI_COMPANY_RESULTS_ROOT": "tmp/external-runs"}, clear=True):
            self.assertEqual(get_results_root(), (REPO_ROOT / "tmp" / "external-runs").resolve())


if __name__ == "__main__":
    unittest.main()
