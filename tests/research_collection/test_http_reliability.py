import urllib.error

from fx_research.infrastructure.http_client import HttpGetPolicy, UrllibHttpClient


class SuccessfulResponse:
    status = 200
    headers = {"Content-Type": "text/plain"}

    def __enter__(self) -> "SuccessfulResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return "https://official.example/item"

    def read(self) -> bytes:
        return b"ok"


def test_http_get_retries_a_transient_failure_within_the_configured_bound(monkeypatch) -> None:
    attempts = 0

    def urlopen(*args: object, **kwargs: object) -> SuccessfulResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.URLError("temporary")
        return SuccessfulResponse()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = UrllibHttpClient(
        HttpGetPolicy(maximum_attempts=2, backoff_seconds=0), sleeper=lambda _: None
    )

    response = client.get("https://official.example/item")

    assert response.status_code == 200
    assert attempts == 2
