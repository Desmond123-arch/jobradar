"""Python with no certificates (a Mac with python.org's Python): borrow the system's, and explain what is wrong."""

import ssl
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import net  # noqa: E402


def verify(cafile=None, capath=None):
    return SimpleNamespace(cafile=cafile, capath=capath)


class FindBundleTests(unittest.TestCase):
    def find(self, files=(), dirs=None, env=None, certifi=None, vp=None):
        dirs = dirs or {}
        return net.find_bundle(env=env or {}, verify=vp or verify("/py/cert.pem", "/py/certs"),
                               exists=lambda p: p in files, isdir=lambda p: p in dirs, listdir=lambda p: dirs[p],
                               certifi_path=lambda: certifi)

    def test_nothing_changes_when_python_already_has_certificates(self):
        self.assertIsNone(self.find(files={"/py/cert.pem", "/etc/ssl/cert.pem"}))
        self.assertIsNone(self.find(dirs={"/py/certs": ["a.pem"]}, files={"/etc/ssl/cert.pem"}))

    def test_an_empty_certificate_folder_does_not_count(self):
        self.assertEqual(self.find(dirs={"/py/certs": []}, files={"/etc/ssl/cert.pem"}), "/etc/ssl/cert.pem")

    def test_the_mac_case_python_has_none_so_the_system_list_is_used(self):
        self.assertEqual(self.find(files={"/etc/ssl/cert.pem"}), "/etc/ssl/cert.pem")

    def test_certifi_is_preferred_when_installed(self):
        self.assertEqual(self.find(files={"/etc/ssl/cert.pem", "/x/certifi.pem"}, certifi="/x/certifi.pem"), "/x/certifi.pem")

    def test_other_systems_lists_are_tried_in_order(self):
        self.assertEqual(self.find(files={"/etc/pki/tls/certs/ca-bundle.crt"}), "/etc/pki/tls/certs/ca-bundle.crt")

    def test_a_missing_certifi_file_is_skipped(self):
        self.assertEqual(self.find(files={"/etc/ssl/cert.pem"}, certifi="/gone.pem"), "/etc/ssl/cert.pem")

    def test_nothing_available_means_nothing_to_do(self):
        self.assertIsNone(self.find())

    def test_the_users_own_settings_are_respected(self):
        for var in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            self.assertIsNone(self.find(files={"/etc/ssl/cert.pem"}, env={var: "/mine"}), var)

    def test_an_unreadable_folder_is_not_a_crash(self):
        def boom(p):
            raise PermissionError(p)
        self.assertFalse(net.python_has_certs(verify(None, "/locked"), exists=lambda p: False, isdir=lambda p: True, listdir=boom))

    def test_it_agrees_with_this_computers_real_python(self):
        # whichever it is, it must not raise
        net.find_bundle()


class InstallTests(unittest.TestCase):
    def test_install_does_nothing_when_no_bundle_is_needed(self):
        self.assertIsNone(net.install(env={"SSL_CERT_FILE": "/mine"}))

    def test_a_broken_bundle_is_ignored_not_fatal(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write("not a certificate")
        self.addCleanup(Path(f.name).unlink)
        self.assertIsNone(net.install(env={}, verify=verify("/gone", "/gone"), exists=lambda p: p == f.name,
                                      isdir=lambda p: False, certifi_path=lambda: f.name))


class CollapseTests(unittest.TestCase):
    def test_many_certificate_failures_become_one_message_with_the_fix(self):
        bad = [f"Co{i} (ashby:co{i}): could not reach https://x: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed>" for i in range(109)]
        out = net.collapse(bad + ["Other (lever:o): board not found"])
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0].startswith("109 requests failed."))
        self.assertIn("Install Certificates.command", out[0])
        self.assertIn("pip install certifi", out[0])
        self.assertEqual(out[1], "Other (lever:o): board not found")

    def test_other_problems_are_left_alone(self):
        self.assertEqual(net.collapse(["a", "b"]), ["a", "b"])
        self.assertEqual(net.collapse([]), [])


class SelfUpdateTests(unittest.TestCase):
    def test_the_update_command_explains_a_certificate_failure(self):
        import selfupdate

        def fail(url):
            raise OSError("<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed>")
        out = []
        code = selfupdate.main(["--check"], say=out.append, fetch=fail, kind="pip", src=Path("/x"), runner=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
        self.assertEqual(code, 1)
        self.assertIn(net.FIX, out)


if __name__ == "__main__":
    unittest.main()
