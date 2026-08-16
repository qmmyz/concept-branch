import io
import zipfile

import pytest
from pypdf import PdfWriter
from pypdf import filters as pypdf_filters
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from concept_branch.attachments import (
    MAX_CONTEXT_CHARS,
    MAX_EXTRACTED_CHARS,
    MAX_FILE_BYTES,
    MAX_PDF_DECOMPRESSED_BYTES,
    MAX_PDF_STREAM_DECOMPRESSED_BYTES,
    AttachmentError,
    build_attachment_context,
    extract_attachment,
)


def _make_text_pdf(text: bytes) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


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
    with pytest.raises(AttachmentError, match="JSON 文件格式无效"):
        extract_attachment("huge-integer.json", b'{"n":' + b"9" * 100_000 + b"}")


def test_docx_parser_failures_are_reported_as_attachment_errors():
    unknown_encoding = io.BytesIO()
    with zipfile.ZipFile(unknown_encoding, "w") as archive:
        archive.writestr("word/document.xml", b'<?xml version="1.0" encoding="unknown-codec"?><document/>')
    with pytest.raises(AttachmentError, match="DOCX 文件损坏或格式无效"):
        extract_attachment("unknown-encoding.docx", unknown_encoding.getvalue())

    unsupported_compression = io.BytesIO()
    with zipfile.ZipFile(unsupported_compression, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"<document/>")
    payload = bytearray(unsupported_compression.getvalue())
    payload[8:10] = (99).to_bytes(2, "little")
    central_header = payload.index(b"PK\x01\x02")
    payload[central_header + 10:central_header + 12] = (99).to_bytes(2, "little")
    with pytest.raises(AttachmentError, match="DOCX 文件损坏或格式无效"):
        extract_attachment("unsupported-compression.docx", bytes(payload))


def test_pdf_decompression_budget_rejects_small_compressed_bomb():
    previous_limits = {
        "ZLIB_MAX_OUTPUT_LENGTH": pypdf_filters.ZLIB_MAX_OUTPUT_LENGTH,
        "LZW_MAX_OUTPUT_LENGTH": pypdf_filters.LZW_MAX_OUTPUT_LENGTH,
        "RUN_LENGTH_MAX_OUTPUT_LENGTH": pypdf_filters.RUN_LENGTH_MAX_OUTPUT_LENGTH,
        "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH": pypdf_filters.MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH,
    }
    payload = _make_text_pdf(b"A" * (MAX_PDF_STREAM_DECOMPRESSED_BYTES + 1))
    assert len(payload) < 20_000
    with pytest.raises(AttachmentError, match="解压后的内容超过安全上限"):
        extract_attachment("compressed-bomb.pdf", payload)
    for name, value in previous_limits.items():
        assert getattr(pypdf_filters, name) == value


def test_pdf_cumulative_decompression_budget_is_enforced():
    writer = PdfWriter()
    per_page = MAX_PDF_DECOMPRESSED_BYTES // 3 + 1
    for _ in range(3):
        page = writer.add_blank_page(width=612, height=792)
        stream = DecodedStreamObject()
        stream.set_data(b"%" + b"A" * per_page + b"\n")
        page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    buffer = io.BytesIO()
    writer.write(buffer)
    assert len(buffer.getvalue()) < 20_000
    with pytest.raises(AttachmentError, match="解压后的内容超过安全上限"):
        extract_attachment("cumulative-bomb.pdf", buffer.getvalue())


def test_pdf_text_extraction_stops_at_character_limit():
    payload = _make_text_pdf(b"A" * (MAX_EXTRACTED_CHARS + 1_000))
    text, truncated, file_format = extract_attachment("long.pdf", payload)
    assert text == "A" * MAX_EXTRACTED_CHARS
    assert truncated
    assert file_format == "pdf"


def test_attachment_size_and_extracted_text_bounds():
    with pytest.raises(AttachmentError, match="10 MB"):
        extract_attachment("oversized.txt", b"x" * (MAX_FILE_BYTES + 1))

    text, truncated, _ = extract_attachment("long.txt", b"x" * (MAX_EXTRACTED_CHARS + 1))
    assert len(text) == MAX_EXTRACTED_CHARS
    assert truncated


def test_truncation_flag_survives_whitespace_cleanup():
    text, truncated, _ = extract_attachment("tail.txt", b"A" * MAX_EXTRACTED_CHARS + b"\nB")
    assert text == "A" * MAX_EXTRACTED_CHARS
    assert truncated

    buffer = io.BytesIO()
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:r><w:t xml:space="preserve">'
        + "A" * MAX_EXTRACTED_CHARS
        + "\nB</w:t></w:r></w:p></w:body></w:document>"
    ).encode()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    text, truncated, _ = extract_attachment("tail.docx", buffer.getvalue())
    assert text == "A" * MAX_EXTRACTED_CHARS
    assert truncated


def test_aggregate_attachment_context_is_bounded():
    context = build_attachment_context([
        {"filename": "first.txt", "extracted_text": "a" * 40_000, "truncated": False},
        {"filename": "second.txt", "extracted_text": "b" * 40_000, "truncated": False},
    ])
    assert context.count("a") == 40_000
    assert context.count("b") == MAX_CONTEXT_CHARS - 40_000
    assert "[该文件内容已截断]" in context
