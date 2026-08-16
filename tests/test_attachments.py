import io
import zipfile

import pytest
from pypdf import PdfWriter

from concept_branch.attachments import (
    MAX_CONTEXT_CHARS,
    MAX_EXTRACTED_CHARS,
    MAX_FILE_BYTES,
    AttachmentError,
    build_attachment_context,
    extract_attachment,
)


def test_text_and_json_extraction():
    text, truncated, file_format = extract_attachment("背景.md", "第一段\n\n第二段".encode())
    assert text == "第一段\n\n第二段"
    assert not truncated
    assert file_format == "md"

    text, _, file_format = extract_attachment("data.json", b'{"name":"enzyme"}')
    assert '"name": "enzyme"' in text
    assert file_format == "json"


def test_docx_extraction_without_external_office_runtime():
    buffer = io.BytesIO()
    xml = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:r><w:t>DOCX background text</w:t></w:r></w:p></w:body></w:document>'''
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    text, _, file_format = extract_attachment("material.docx", buffer.getvalue())
    assert text == "DOCX background text"
    assert file_format == "docx"


def test_docx_rejects_entity_and_doctype_declarations():
    buffer = io.BytesIO()
    xml = b'''<?xml version="1.0"?>
    <!DOCTYPE w:document [<!ENTITY repeated "expanded">]>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:r><w:t>&repeated;</w:t></w:r></w:p></w:body></w:document>'''
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    with pytest.raises(AttachmentError, match="不允许的 XML 声明"):
        extract_attachment("material.docx", buffer.getvalue())


def test_docx_rejects_utf16_entity_and_doctype_declarations():
    buffer = io.BytesIO()
    xml = '''<?xml version="1.0" encoding="UTF-16"?>
    <!DOCTYPE w:document [<!ENTITY repeated "expanded">]>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
    <w:p><w:r><w:t>&repeated;</w:t></w:r></w:p></w:body></w:document>'''.encode("utf-16")
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    with pytest.raises(AttachmentError, match="不允许的 XML 声明"):
        extract_attachment("material.docx", buffer.getvalue())


def test_scanned_or_empty_pdf_has_clear_error():
    buffer = io.BytesIO()
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.write(buffer)
    with pytest.raises(AttachmentError, match="未识别到可提取文本"):
        extract_attachment("scan.pdf", buffer.getvalue())


def test_unsupported_and_invalid_files_are_rejected():
    with pytest.raises(AttachmentError, match="仅支持"):
        extract_attachment("archive.zip", b"not allowed")
    with pytest.raises(AttachmentError, match="JSON 文件格式无效"):
        extract_attachment("bad.json", b"{not-json}")


def test_attachment_size_and_extracted_text_bounds():
    with pytest.raises(AttachmentError, match="10 MB"):
        extract_attachment("oversized.txt", b"x" * (MAX_FILE_BYTES + 1))

    text, truncated, _ = extract_attachment("long.txt", b"x" * (MAX_EXTRACTED_CHARS + 1))
    assert len(text) == MAX_EXTRACTED_CHARS
    assert truncated


def test_aggregate_attachment_context_is_bounded():
    context = build_attachment_context([
        {"filename": "first.txt", "extracted_text": "a" * 40_000, "truncated": False},
        {"filename": "second.txt", "extracted_text": "b" * 40_000, "truncated": False},
    ])
    assert context.count("a") == 40_000
    assert context.count("b") == MAX_CONTEXT_CHARS - 40_000
    assert "[该文件内容已截断]" in context
