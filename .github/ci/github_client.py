"""The one GitHub API request path for the trusted CI scripts."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_VERSION = "2026-03-10"


def request(method, path, body=None, tolerate=None):
    """Send one request with the job token.

    `tolerate(code, method, path)` returns `(True, value)` for an HTTP error the caller
    expects, such as a missing resource, and `(False, None)` otherwise.
    """
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RuntimeError("The GitHub job token is required.")
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    url = (os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql")
           if path == "graphql" else f"{base}/{path}")
    outgoing = Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "Content-Type": "application/json", "X-GitHub-Api-Version": API_VERSION,
        },
    )
    try:
        with urlopen(outgoing, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else None
    except HTTPError as error:
        if tolerate is not None:
            handled, value = tolerate(error.code, method, path)
            if handled:
                return value
        # Never echo API error bodies.
        raise RuntimeError(f"GitHub API request failed (HTTP {error.code}).") from None
    except (URLError, OSError, ValueError):
        raise RuntimeError("GitHub API request failed.") from None


def pages(api, path, key=None):
    """Yield every item of a paged GET; `key` names the list when the response is an object."""
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        response = api("GET", f"{path}{separator}per_page=100&page={page}")
        items = response[key] if key else response
        yield from items
        if len(items) < 100:
            break
        page += 1
