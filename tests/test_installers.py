import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRECTORIES = (
    "node_modules",
    "build",
    ".cache",
    ".tmp",
    "__pycache__",
    ".pytest_cache",
    ".remotion",
)


class InstallerPayloadTests(unittest.TestCase):
    def _fixture(self, directory, installer_name):
        repo = Path(directory) / "repo"
        installer = repo / "installers" / installer_name
        installer.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "installers" / installer_name, installer)

        core = repo / ".agents" / "skills" / "xhs-question-solutions"
        wrapper = repo / ".claude" / "skills" / "xhs-question-solutions"
        required = (
            "SKILL.md",
            "package.json",
            "package-lock.json",
            "scripts/render_video.py",
            "scripts/render_video.mjs",
            "remotion/index.jsx",
            "remotion/scenes/ActionScene.jsx",
        )
        for relative in required:
            target = core / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"fixture:{relative}\n", encoding="utf-8")
        wrapper.mkdir(parents=True)
        (wrapper / "SKILL.md").write_text("wrapper\n", encoding="utf-8")

        for name in EXCLUDED_DIRECTORIES:
            cache = core / name
            cache.mkdir(parents=True)
            (cache / "must-not-install.txt").write_text("private cache\n", encoding="utf-8")
        nested_cache = core / "remotion" / "node_modules"
        nested_cache.mkdir(parents=True)
        (nested_cache / "must-not-install.txt").write_text("nested cache\n", encoding="utf-8")
        return installer, Path(directory) / "installed", required

    def _assert_payload(self, installed, required):
        core = installed / ".agents" / "skills" / "xhs-question-solutions"
        for relative in required:
            self.assertTrue((core / relative).is_file(), f"missing required payload: {relative}")
        for name in EXCLUDED_DIRECTORIES:
            self.assertFalse((core / name).exists(), f"installed local cache: {name}")
        self.assertFalse((core / "remotion" / "node_modules").exists())

    @unittest.skipUnless(os.name == "nt", "PowerShell installer is exercised on Windows")
    def test_powershell_installer_keeps_sources_but_excludes_local_dependencies_and_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            installer, installed, required = self._fixture(directory, "install.ps1")
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer), "-DestinationRoot", str(installed)],
                check=True,
                capture_output=True,
                text=True,
            )
            self._assert_payload(installed, required)

    @unittest.skipIf(os.name == "nt", "POSIX installer is exercised on Ubuntu")
    def test_posix_installer_keeps_sources_but_excludes_local_dependencies_and_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            installer, installed, required = self._fixture(directory, "install.sh")
            subprocess.run(
                ["sh", str(installer), "--root", str(installed)],
                check=True,
                capture_output=True,
                text=True,
            )
            self._assert_payload(installed, required)


if __name__ == "__main__":
    unittest.main()
