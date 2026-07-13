from io import BytesIO

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .errors import DetailContentError
from .http import HttpResponse


class OfficialDetailExtractor:
    def extract(self, response: HttpResponse) -> str:
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or response.url.lower().endswith(".pdf"):
            return self._pdf_text(response.body)
        return self._html_text(response.body)

    @staticmethod
    def _html_text(body: bytes) -> str:
        soup = BeautifulSoup(body, "html.parser")
        root = soup.find("main") or soup.find(id="contents") or soup.find(id="content")
        if root is None:
            raise DetailContentError("Official detail page has no recognized content root")
        for element in root.select("nav, footer, header, script, style, form"):
            element.decompose()
        text = " ".join(root.stripped_strings)
        if not text:
            raise DetailContentError("Official detail page contains no analyzable text")
        return text

    @staticmethod
    def _pdf_text(body: bytes) -> str:
        try:
            text = " ".join((page.extract_text() or "") for page in PdfReader(BytesIO(body)).pages)
        except Exception as error:
            raise DetailContentError("Official PDF detail could not be parsed") from error
        if not text.strip():
            raise DetailContentError("Official PDF detail contains no extractable text")
        return text
