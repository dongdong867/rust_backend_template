"""Read small job logs as untrusted data; credentials never follow storage redirects."""

import os
import re
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, OpenerDirector, ProxyHandler, Request


MAX_LOG_BYTES = 2 * 1024 * 1024


def safe_url(url):
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https" and bool(parsed.hostname)
        and parsed.username is None and parsed.password is None
    )


def read_job_log(repo, job_id):
    """Unavailable/oversized logs mean unavailable metrics, never a changed run result."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(
        r"[1-9][0-9]{0,19}", str(job_id)
    ):
        return None
    token = os.environ.get("GH_TOKEN", "")
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    if not token or not safe_url(api_url):
        return None
    url = f"{api_url}/repos/{repo}/actions/jobs/{job_id}/logs"
    headers = {
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    # No redirect/error processor: inspect statuses ourselves rather than forwarding API headers.
    opener = OpenerDirector()
    opener.add_handler(ProxyHandler())
    opener.add_handler(HTTPSHandler())
    try:
        for _ in range(4):
            with opener.open(Request(url, headers=headers), timeout=30) as response:
                if response.status in (301, 302, 303, 307, 308):
                    next_url = response.headers.get("Location", "")
                    if not safe_url(next_url):
                        return None
                    url = next_url
                    headers = {}
                    continue
                if response.status != 200:
                    return None
                length = response.headers.get("Content-Length")
                if length is not None and int(length) > MAX_LOG_BYTES:
                    return None
                content = response.read(MAX_LOG_BYTES + 1)
                if len(content) > MAX_LOG_BYTES:
                    return None
                return content.decode("utf-8", errors="replace")
    except (OSError, ValueError, TypeError):
        return None
    return None
