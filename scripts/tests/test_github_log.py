"""Bounded job-log transport must never forward privileged credentials to log storage."""

import importlib.util
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/ci/github_log.py"


def response(status=200, data=b"log", headers=None):
    stream = io.BytesIO(data)
    value = Mock()
    value.status = status
    value.headers = headers or {}
    value.read.side_effect = stream.read
    value.__enter__ = Mock(return_value=value)
    value.__exit__ = Mock(return_value=False)
    return value


class GithubLogTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "Bounded log transport missing")
        spec = importlib.util.spec_from_file_location("github_log", SCRIPT)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        self.env = patch.dict(os.environ, {"GH_TOKEN": "private-token"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_transport(self, *responses):
        opener = SimpleNamespace(open=Mock(side_effect=responses), add_handler=Mock())
        with patch.object(self.helper, "OpenerDirector", return_value=opener):
            result = self.helper.read_job_log("owner/repo", "100")
        return result, opener.open.call_args_list

    def test_auth_only_on_initial_api_request(self):
        result, calls = self.run_transport(
            response(302, headers={"Location": "https://logs.example/temporary-signed-log"}),
            response(data=b"bounded log"),
        )
        self.assertEqual(result, "bounded log")
        self.assertEqual(calls[0].args[0].get_header("Authorization"), "Bearer private-token")
        self.assertEqual(calls[0].args[0].full_url, "https://api.github.com/repos/owner/repo/actions/jobs/100/logs")
        self.assertIsNone(calls[1].args[0].get_header("Authorization"))
        self.assertEqual(calls[1].kwargs["timeout"], 30)

    def test_direct_api_log_and_limits(self):
        result, _ = self.run_transport(response(data=b"log"))
        self.assertEqual(result, "log")
        large = response(data=b"x" * (self.helper.MAX_LOG_BYTES + 1))
        result, _ = self.run_transport(large)
        self.assertIsNone(result)
        large.read.assert_called_once_with(self.helper.MAX_LOG_BYTES + 1)
        declared = response(headers={"Content-Length": str(self.helper.MAX_LOG_BYTES + 1)})
        result, _ = self.run_transport(declared)
        self.assertIsNone(result)
        declared.read.assert_not_called()

    def test_unsafe_redirects_and_redirect_loops_fail_closed(self):
        for url in ("http://logs.example/log", "https://user:pass@logs.example/log", "file:///tmp/log", "/relative"):
            with self.subTest(url=url):
                result, calls = self.run_transport(response(302, headers={"Location": url}))
                self.assertIsNone(result)
                self.assertEqual(len(calls), 1)
        result, calls = self.run_transport(*[
            response(302, headers={"Location": "https://logs.example/log"}) for _ in range(4)
        ])
        self.assertIsNone(result)
        self.assertEqual(len(calls), 4)

    def test_missing_failed_or_malformed_transport_never_echoes_data(self):
        for value in (response(403, data=b"secret response"), response(404), OSError("private-token")):
            result, _ = self.run_transport(value)
            self.assertIsNone(result)
        for repo, job in (("owner/repo;id", "100"), ("owner/repo", "../1"), ("owner/repo", "0")):
            with patch.object(self.helper, "OpenerDirector") as opener:
                self.assertIsNone(self.helper.read_job_log(repo, job))
                opener.assert_not_called()
