"""What counts as an endpoint on this machine — parsed, never prefix-matched."""

from __future__ import annotations

import pytest

from backend.core.local_endpoint import is_local_base_url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "https://LOCALHOST/v1",
        "http://127.0.0.1:8000/v1",
        "http://127.0.0.2/v1",
        "http://[::1]:8080/v1",
        "http://[::ffff:127.0.0.1]/v1",
        "http://0.0.0.0:8080/v1",
        "  http://localhost:1234  ",
    ],
)
def test_a_real_local_endpoint_is_local(url):
    assert is_local_base_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost.evil.example",
        "http://localhost.evil.example:11434/v1",
        "http://localhost@api.example/v1",
        "http://localhost:80@api.example/v1",
        "http://user:pw@localhost/v1",
        "http://127.foo.example",
        "http://127.0.0.1.evil.example/v1",
        "http://10.0.0.5/v1",
        "http://192.168.1.10:11434/v1",
        "ftp://localhost/v1",
        "localhost:11434",
        "//localhost/v1",
        "http://localhost:notaport/v1",
        "https://api.openai.com/v1",
        "",
        "   ",
    ],
)
def test_a_lookalike_or_remote_endpoint_is_not_local(url):
    assert is_local_base_url(url) is False
