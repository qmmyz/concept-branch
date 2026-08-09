from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_CHARS = 50_000
MAX_CONTEXT_CHARS = 60_000
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown", ".csv", ".json", ".docx"}


class AttachmentError(ValueError):
    pass


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


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 25 * 1024 * 1024:
                raise AttachmentError("DOCX 解压后的正文过大")
            root = ElementTree.fromstring(archive.read(info))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise AttachmentError("DOCX 文件损坏或格式无效") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise AttachmentError("暂不支持加密 PDF") from exc
        if len(reader.pages) > 500:
            raise AttachmentError("PDF 页数超过 500 页上限")
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except AttachmentError:
        raise
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
    if suffix == ".pdf":
        text = _extract_pdf(data)
    elif suffix == ".docx":
        text = _extract_docx(data)
    else:
        text = _decode_text(data)
        if suffix == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError as exc:
                raise AttachmentError("JSON 文件格式无效") from exc
    text = _clean_text(text)
    if not text:
        message = "PDF 未识别到可提取文本；扫描版文件暂不支持 OCR" if suffix == ".pdf" else "文件中没有可用文本"
        raise AttachmentError(message)
    truncated = len(text) > MAX_EXTRACTED_CHARS
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
