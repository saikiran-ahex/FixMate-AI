from __future__ import annotations

from io import BytesIO
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None


SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".yaml", ".yml", ".log"}


def extract_text_from_file(file_path: str | Path, allowed_base: str | Path | None = None) -> str:
    path = Path(file_path).resolve()
    if allowed_base is not None:
        base = Path(allowed_base).resolve()
        if not str(path).startswith(str(base)):
            raise ValueError(f"Path {path} is outside the allowed directory {base}")
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return _extract_pdf_text(path)

    if suffix in SUPPORTED_TEXT_EXTENSIONS:
        return _sanitize_text(path.read_text(encoding="utf-8", errors="ignore"))

    raw_bytes = path.read_bytes()
    return _sanitize_text(raw_bytes.decode("utf-8", errors="ignore"))


def _extract_pdf_text(path: Path) -> str:
    if PdfReader is None:
        return ""

    try:
        reader = PdfReader(BytesIO(path.read_bytes()))
    except Exception:
        return ""

    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return _sanitize_text("\n\n".join(pages))


def _sanitize_text(text: str) -> str:
    if not text:
        return ""
    sanitized = text.replace("\x00", "")
    sanitized = "\n".join(line.rstrip() for line in sanitized.splitlines())
    return sanitized.strip()
