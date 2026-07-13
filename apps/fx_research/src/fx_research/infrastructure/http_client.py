import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from ..errors import SourceRetrievalError
from ..http import HttpResponse


@dataclass(frozen=True, slots=True)
class HttpGetPolicy:
    timeout_seconds: float = 10.0
    maximum_attempts: int = 3
    backoff_seconds: float = 0.25

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.maximum_attempts < 1 or self.backoff_seconds < 0:
            raise ValueError("HTTP GET policy values are invalid")


class UrllibHttpClient:
    _retryable_statuses = {408, 429, 500, 502, 503, 504}

    def __init__(
        self,
        policy: HttpGetPolicy | None = None,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._policy = policy or HttpGetPolicy()
        self._sleeper = sleeper

    def get(self, url: str) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(1, self._policy.maximum_attempts + 1):
            try:
                request = urllib.request.Request(
                    url, headers={"User-Agent": "FT-FXNewslab/0.1 official-source-collector"}
                )
                with urllib.request.urlopen(
                    request, timeout=self._policy.timeout_seconds
                ) as response:
                    return HttpResponse(
                        url=response.geturl(),
                        status_code=response.status,
                        headers={key.lower(): value for key, value in response.headers.items()},
                        body=response.read(),
                    )
            except urllib.error.HTTPError as error:
                if error.code not in self._retryable_statuses:
                    return HttpResponse(url, error.code, {}, error.read())
                last_error = error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
            if attempt < self._policy.maximum_attempts:
                self._sleeper(self._policy.backoff_seconds * attempt)
        raise SourceRetrievalError(
            f"HTTP GET failed after {self._policy.maximum_attempts} attempts: {url}"
        ) from last_error
