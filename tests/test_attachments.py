import io
import zipfile

import pytest
from pypdf import PdfWriter

from concept_branch.attachments import AttachmentError, extract_attachment


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
