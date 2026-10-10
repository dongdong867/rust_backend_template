"""Installer integrity checks use synthetic archives, never the network."""

import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import unittest


class ActionlintInstallerTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "install_actionlint", Path(__file__).resolve().parents[1] / "install_actionlint.py"
        )
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)

    def archive(self, name="actionlint", kind=tarfile.REGTYPE):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = 6 if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(b"binary") if member.size else None)
        return output.getvalue()

    def test_supported_platforms_and_exact_version(self):
        self.assertEqual(
            self.installer.archive_name("1.7.12", "Darwin", "arm64"),
            "actionlint_1.7.12_darwin_arm64.tar.gz",
        )
        self.assertEqual(
            self.installer.archive_name("1.7.12", "Linux", "x86_64"),
            "actionlint_1.7.12_linux_amd64.tar.gz",
        )
        for version in ("latest", "../1.7.12", "1.7.12;false"):
            with self.assertRaises(ValueError):
                self.installer.archive_name(version, "Linux", "x86_64")
        with self.assertRaises(ValueError):
            self.installer.archive_name("1.7.12", "Windows", "x86_64")

    def test_verified_archive_returns_only_the_binary(self):
        data = self.archive()
        checksum = hashlib.sha256(data).hexdigest()
        self.assertEqual(
            self.installer.verified_binary(data, f"{checksum}  fixture.tar.gz\n", "fixture.tar.gz"),
            b"binary",
        )

    def test_missing_or_incorrect_checksum_is_rejected(self):
        for checksums in ("", f"{'0' * 64}  fixture.tar.gz\n"):
            with self.assertRaisesRegex(ValueError, "checksum"):
                self.installer.verified_binary(self.archive(), checksums, "fixture.tar.gz")

    def test_path_or_link_instead_of_binary_is_rejected(self):
        for name, kind in (("../actionlint", tarfile.REGTYPE), ("actionlint", tarfile.SYMTYPE)):
            data = self.archive(name, kind)
            checksums = f"{hashlib.sha256(data).hexdigest()}  fixture.tar.gz\n"
            with self.assertRaisesRegex(ValueError, "binary"):
                self.installer.verified_binary(data, checksums, "fixture.tar.gz")


if __name__ == "__main__":
    unittest.main()
