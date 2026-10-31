#!/usr/bin/env python3
"""Tests for the shared GitHub request helper."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/ci"))
import github_client  # noqa: E402


def paged(items, key=None):
    calls = []

    def api(method, path):
        calls.append((method, path))
        page = int(path.rsplit("page=", 1)[1])
        chunk = items[(page - 1) * 100:page * 100]
        return {key: chunk} if key else chunk

    return api, calls


class PagesTest(unittest.TestCase):
    def test_reads_a_bare_list_response(self):
        api, calls = paged([1, 2])
        self.assertEqual(list(github_client.pages(api, "repos/o/s/statuses")), [1, 2])
        self.assertEqual(calls, [("GET", "repos/o/s/statuses?per_page=100&page=1")])

    def test_reads_the_named_key_of_a_response(self):
        api, _ = paged([1, 2], "jobs")
        self.assertEqual(list(github_client.pages(api, "repos/o/s/jobs", "jobs")), [1, 2])

    def test_keeps_an_existing_query(self):
        api, calls = paged([], "workflow_runs")
        self.assertEqual(list(github_client.pages(api, "runs?head_sha=a", "workflow_runs")), [])
        self.assertEqual(calls, [("GET", "runs?head_sha=a&per_page=100&page=1")])

    def test_stops_only_after_a_short_page(self):
        items = list(range(150))
        api, calls = paged(items)
        self.assertEqual(list(github_client.pages(api, "statuses")), items)
        self.assertEqual(len(calls), 2)

    def test_a_full_last_page_needs_one_empty_read(self):
        items = list(range(100))
        api, calls = paged(items)
        self.assertEqual(list(github_client.pages(api, "statuses")), items)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
