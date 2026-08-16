from __future__ import annotations

import io
import json
import re
import threading
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import Path

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader
from pypdf import filters as pypdf_filters
from pypdf.errors import LimitReachedError
from pypdf.generic import StreamObject


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_CHARS = 50_000
MAX_CONTEXT_CHARS = 60_000
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown", ".csv", ".json", ".docx"}

# PDF decompression budgets. A PDF upload may be up to 10 MB on disk, but its
# content streams are compressed: a tiny file can inflate to hundreds of MB.
# Bound both the largest single decoded content stream and the cumulative
# decoded content-stream bytes examined for one document.
MAX_PDF_DECOMPRESSED_BYTES = 8 * 1024 * 1024
MAX_PDF_STREAM_DECOMPRESSED_BYTES = 4 * 1024 * 1024

_PDF_DECODE_LIMITS = {
    "ZLIB_MAX_OUTPUT_LENGTH": MAX_PDF_STREAM_DECOMPRESSED_BYTES,
    "LZW_MAX_OUTPUT_LENGTH": MAX_PDF_STREAM_DECOMPRESSED_BYTES,
    "RUN_LENGTH_MAX_OUTPUT_LENGTH": MAX_PDF_STREAM_DECOMPRESSED_BYTES,
    # A page's /Contents may be an array of streams; the combined page content
    # still must fit in the per-document budget.
    "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH": MAX_PDF_DECOMPRESSED_BYTES,
}
_PDF_EXTRACTION_LOCK = threading.Lock()


class AttachmentError(ValueError):
    pass


@contextmanager
def _pdf_decode_limits():
    """Apply per-stream decode limits while PDF content is being processed.

    The limits are module globals inside pypdf, so PDF extraction is
    serialized for the (brief) time they are lowered. This keeps concurrent
    requests safe while bounding peak memory per decoded stream.
    """
    missing = [name for name in _PDF_DECODE_LIMITS if not hasattr(pypdf_filters, name)]
    if missing:
        raise AttachmentError("PDF 解析器缺少安全限制支持")
    with _PDF_EXTRACTION_LOCK:
        previous = {
            name: getattr(pypdf_filters, name)
            for name in _PDF_DECODE_LIMITS
        }
        try:
            for name, limit in _PDF_DECODE_LIMITS.items():
                setattr(pypdf_filters, name, limit)
            yield
        finally:
            for name, value in previous.items():
                setattr(pypdf_filters, name, value)


def _clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _decode_text(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise AttachmentError("文件看起来不是文本文件")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return data.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise AttachmentError("文本文件必须使用 UTF-8 或 GB18030 编码") from exc


def _extract_docx(data: bytes) -> tuple[str, bool]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 25 * 1024 * 1024:
                raise AttachmentError("DOCX 解压后的正文过大")
            document_xml = archive.read(info)
            root = ElementTree.fromstring(
                document_xml,
                forbid_dtd=True,
                forbid_entities=True,
                forbid_external=True,
            )
    except DefusedXmlException as exc:
        raise AttachmentError("DOCX 正文包含不允许的 XML 声明") from exc
    except (
        zipfile.BadZipFile,
        KeyError,
        ElementTree.ParseError,
        ValueError,
        LookupError,
        zlib.error,
        NotImplementedError,
        RuntimeError,
    ) as exc:
        raise AttachmentError("DOCX 文件损坏或格式无效") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    remaining = MAX_EXTRACTED_CHARS + 1
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        if text.strip():
            chunk = ("\n" if paragraphs else "") + text
            paragraphs.append(chunk[:remaining])
            remaining -= min(len(chunk), remaining)
            if remaining == 0:
                break
    return "".join(paragraphs), remaining == 0


def _walk_form_xobjects(resources, seen: set[int]):
    if hasattr(resources, "get_object"):
        resources = resources.get_object()
    if not isinstance(resources, dict):
        return
    xobjects = resources.get("/XObject")
    if hasattr(xobjects, "get_object"):
        xobjects = xobjects.get_object()
    if not isinstance(xobjects, dict):
        return
    for value in xobjects.values():
        resolved = value.get_object()
        marker = id(resolved)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(resolved, StreamObject) and str(resolved.get("/Subtype")) == "/Form":
            yield resolved
            yield from _walk_form_xobjects(resolved.get("/Resources"), seen)


def _charge_page_content(page, budget_used: int) -> int:
    """Decode/cache one page's content streams and charge the running budget."""
    content = page.get_contents()
    if content is not None:
        budget_used += len(content.get_data())
        if budget_used > MAX_PDF_DECOMPRESSED_BYTES:
            raise AttachmentError("PDF 解压后的内容超过安全上限")

    resources = None
    get_inherited = getattr(page, "get_inherited", None)
    if get_inherited is not None:
        resources = get_inherited("/Resources")
    if resources is None:
        resources = page.get("/Resources")
    for form in _walk_form_xobjects(resources, set()):
        budget_used += len(form.get_data())
        if budget_used > MAX_PDF_DECOMPRESSED_BYTES:
            raise AttachmentError("PDF 解压后的内容超过安全上限")
    return budget_used


def _extract_pdf(data: bytes) -> tuple[str, bool]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise AttachmentError("暂不支持加密 PDF") from exc
        pages = list(reader.pages)
        if len(pages) > 500:
            raise AttachmentError("PDF 页数超过 500 页上限")

        chunks: list[str] = []
        remaining = MAX_EXTRACTED_CHARS + 1
        truncated = False
        budget_used = 0
        with _pdf_decode_limits():
            for page in pages:
                budget_used = _charge_page_content(page, budget_used)
                page_text = page.extract_text() or ""
                if page_text:
                    chunk = ("\n\n" if chunks else "") + page_text
                    if len(chunk) >= remaining:
                        chunks.append(chunk[:remaining])
                        truncated = True
                        remaining = 0
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
        return "".join(chunks), truncated
    except AttachmentError:
        raise
    except LimitReachedError as exc:
        raise AttachmentError("PDF 解压后的内容超过安全上限") from exc
    except Exception as exc:
        raise AttachmentError("PDF 文件损坏或无法解析") from exc


def extract_attachment(filename: str, data: bytes) -> tuple[str, bool, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise AttachmentError("仅支持 PDF、TXT、Markdown、CSV、JSON 和 DOCX")
    if not data:
        raise AttachmentError("文件为空")
    if len(data) > MAX_FILE_BYTES:
        raise AttachmentError("单个文件不能超过 10 MB")
    raw_truncated = False
    if suffix == ".pdf":
        text, raw_truncated = _extract_pdf(data)
    elif suffix == ".docx":
        text, raw_truncated = _extract_docx(data)
    else:
        text = _decode_text(data)
        if suffix == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except (ValueError, RecursionError) as exc:
                raise AttachmentError("JSON 文件格式无效") from exc
        raw_truncated = len(text) > MAX_EXTRACTED_CHARS
    text = _clean_text(text)
    if not text:
        message = "PDF 未识别到可提取文本；扫描版文件暂不支持 OCR" if suffix == ".pdf" else "文件中没有可用文本"
        raise AttachmentError(message)
    truncated = raw_truncated or len(text) > MAX_EXTRACTED_CHARS
    return text[:MAX_EXTRACTED_CHARS], truncated, suffix.lstrip(".")


def build_attachment_context(attachments: list[dict]) -> str:
    if not attachments:
        return ""
    remaining = MAX_CONTEXT_CHARS
    sections: list[str] = []
    for attachment in attachments:
        if remaining <= 0:
            break
        text = str(attachment["extracted_text"])
        excerpt = text[:remaining]
        remaining -= len(excerpt)
        suffix = "\n[该文件内容已截断]" if len(excerpt) < len(text) or attachment.get("truncated") else ""
        sections.append(f"### 文件：{attachment['filename']}\n{excerpt}{suffix}")
    return "\n\n".join(sections)
