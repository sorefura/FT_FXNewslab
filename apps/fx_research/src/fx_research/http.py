from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class HttpResponse:
    url: str
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class HttpClient(Protocol):
    def get(self, url: str) -> HttpResponse: ...
