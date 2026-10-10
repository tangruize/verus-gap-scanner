from __future__ import annotations

import io
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class SetupBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / "setup"
        self.script.write_bytes((Path(__file__).resolve().parents[1] / "setup").read_bytes())
        self.environment = self.root / ".venv"
        self.python = self.environment / "bin/python"
        self.pip_check = [str(self.python), "-m", "pip", "--version"]
        self.repair = [str(self.python), "-m", "ensurepip", "--upgrade", "--default-pip"]
        self.install = [
            str(self.python), "-m", "pip", "install", "--quiet", "-e", str(self.root),
        ]
        self.verus = [str(self.python), "-m", "verus_gap.setup_verus"]
        self.ok = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        self.no_pip = subprocess.CompletedProcess([], 1, stdout="", stderr="No module named pip")

    def existing_environment(self):
        self.python.parent.mkdir(parents=True)
        self.python.write_bytes(b"existing interpreter")
        marker = self.environment / "existing-package"
        marker.write_bytes(b"preserved package")
        return marker

    def run_setup(self, results, arguments=(), creation_error=None):
        self.output, self.errors = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", [str(self.script), *arguments]), mock.patch.object(
            sys, "path", sys.path.copy(),
        ), mock.patch("subprocess.run", side_effect=results) as commands, mock.patch(
            "venv.EnvBuilder",
        ) as builder, redirect_stdout(self.output), redirect_stderr(self.errors):
            builder.return_value.create.side_effect = creation_error
            with self.assertRaises(SystemExit) as finished:
                runpy.run_path(str(self.script), run_name="__main__")
        return finished.exception.code, commands, builder

    def test_new_environment_is_created_and_checked_before_installing(self):
        status, commands, builder = self.run_setup([self.ok, self.ok, self.ok])
        self.assertEqual(status, 0)
        builder.assert_called_once_with(with_pip=True)
        builder.return_value.create.assert_called_once_with(self.environment)
        self.assertEqual(
            [call.args[0] for call in commands.call_args_list],
            [self.pip_check, self.install, self.verus],
        )
        self.assertTrue(commands.call_args_list[0].kwargs["capture_output"])
        self.assertTrue(commands.call_args_list[1].kwargs["check"])

    def test_healthy_environment_and_existing_packages_are_reused(self):
        marker = self.existing_environment()
        status, commands, builder = self.run_setup([self.ok, self.ok, self.ok])
        self.assertEqual(status, 0)
        builder.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in commands.call_args_list],
            [self.pip_check, self.install, self.verus],
        )
        self.assertEqual(marker.read_bytes(), b"preserved package")
        self.assertEqual(self.python.read_bytes(), b"existing interpreter")
        self.assertNotIn("Repairing", self.output.getvalue())

    def test_missing_pip_is_bootstrapped_without_recreating_the_environment(self):
        marker = self.existing_environment()
        status, commands, builder = self.run_setup([self.no_pip, self.ok, self.ok, self.ok])
        self.assertEqual(status, 0)
        builder.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in commands.call_args_list],
            [self.pip_check, self.repair, self.install, self.verus],
        )
        self.assertTrue(commands.call_args_list[1].kwargs["check"])
        self.assertEqual(marker.read_bytes(), b"preserved package")
        self.assertEqual(self.python.read_bytes(), b"existing interpreter")
        self.assertIn("Repairing pip", self.output.getvalue())

    def test_failed_repair_is_reported_and_stops_installation(self):
        self.existing_environment()
        failure = subprocess.CalledProcessError(1, self.repair)
        status, commands, builder = self.run_setup([self.no_pip, failure])
        self.assertEqual(status, 2)
        builder.assert_not_called()
        self.assertEqual(
            [call.args[0] for call in commands.call_args_list],
            [self.pip_check, self.repair],
        )
        self.assertIn("Cannot prepare the local Python environment", self.errors.getvalue())
        self.assertIn("ensurepip", self.errors.getvalue())

    def test_dependency_installation_failure_can_be_retried(self):
        marker = self.existing_environment()
        failure = subprocess.CalledProcessError(1, self.install)
        status, commands, builder = self.run_setup([self.ok, failure])
        self.assertEqual(status, 2)
        self.assertEqual(commands.call_count, 2)
        builder.assert_not_called()
        self.assertIn("Cannot prepare the local Python environment", self.errors.getvalue())
        status, commands, builder = self.run_setup([self.ok, self.ok, self.ok])
        self.assertEqual(status, 0)
        self.assertEqual(
            [call.args[0] for call in commands.call_args_list],
            [self.pip_check, self.install, self.verus],
        )
        builder.assert_not_called()
        self.assertEqual(marker.read_bytes(), b"preserved package")

    def test_verus_failure_can_be_retried_without_rebuilding_the_environment(self):
        marker = self.existing_environment()
        status, commands, builder = self.run_setup([
            self.ok, self.ok, subprocess.CompletedProcess([], 2),
        ])
        self.assertEqual(status, 2)
        self.assertEqual(commands.call_args.args[0], self.verus)
        builder.assert_not_called()
        status, commands, builder = self.run_setup([self.ok, self.ok, self.ok])
        self.assertEqual(status, 0)
        builder.assert_not_called()
        self.assertEqual(marker.read_bytes(), b"preserved package")

    def test_environment_creation_failure_is_reported(self):
        status, commands, builder = self.run_setup(
            [], creation_error=OSError("fixture creation failure"),
        )
        self.assertEqual(status, 2)
        builder.return_value.create.assert_called_once_with(self.environment)
        commands.assert_not_called()
        self.assertIn("fixture creation failure", self.errors.getvalue())

    def test_installer_arguments_are_forwarded_after_python_setup(self):
        self.existing_environment()
        arguments = ["--release", "0.2026.10.04.426d8b0"]
        status, commands, builder = self.run_setup([self.ok, self.ok, self.ok], arguments)
        self.assertEqual(status, 0)
        self.assertEqual(commands.call_args.args[0], self.verus + arguments)
        builder.assert_not_called()

    def test_help_does_not_create_or_repair_an_environment(self):
        for argument in ("--help", "-h"):
            with self.subTest(argument=argument), mock.patch(
                "verus_gap.setup_verus.main", return_value=0,
            ) as main:
                status, commands, builder = self.run_setup([], [argument])
                self.assertEqual(status, 0)
                main.assert_called_once_with()
                commands.assert_not_called()
                builder.assert_not_called()
                self.assertFalse(self.environment.exists())


if __name__ == "__main__":
    unittest.main()
