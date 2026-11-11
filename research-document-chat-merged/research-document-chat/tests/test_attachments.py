from digital_msl.attachments import (
    classify_bytes,
    is_pdf,
    load_citation_attachment,
    looks_like_error_payload,
    safe_attachment_filename,
)


class FakeClient:
    def __init__(self, download=None, render=None):
        self.download = download
        self.render = render

    def download_document_with_type(self, document_id: str):
        if callable(self.download):
            return self.download(document_id)
        raise OSError("download failed")

    def render_document_with_type(self, document_id: str):
        if callable(self.render):
            return self.render(document_id)
        raise OSError("render failed")


def test_rejects_html_rendered_as_pdf():
    html = b"<html><body>preview</body></html>"
    assert classify_bytes(html, "text/html") is None
    assert looks_like_error_payload(html)


def test_accepts_valid_pdf_bytes():
    pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n" + (b" " * 200)
    assert is_pdf(pdf)
    assert classify_bytes(pdf, "application/octet-stream") == "application/pdf"


def test_load_prefers_download_pdf_over_html_render():
    pdf = b"%PDF-1.4\n" + (b"x" * 200)
    client = FakeClient(
        download=lambda _id: (pdf, "application/pdf"),
        render=lambda _id: (b"<html></html>", "text/html"),
    )
    item = load_citation_attachment(client, "doc-1", "study.pdf")
    assert item is not None
    name, data, media = item
    assert media == "application/pdf"
    assert data.startswith(b"%PDF-")
    assert name.endswith(".pdf")


def test_load_uses_render_when_download_is_json_error():
    pdf = b"%PDF-1.7\n" + (b"y" * 200)
    client = FakeClient(
        download=lambda _id: (b'{"error":"not found"}', "application/json"),
        render=lambda _id: (pdf, "application/pdf"),
    )
    item = load_citation_attachment(client, "doc-1", "pubmed_study.pdf")
    assert item is not None
    assert item[2] == "application/pdf"


def test_safe_filename_short_and_pdf_extension():
    raw = "pubmed_42920147_PMC13693012_Real-world injection burden_ treatment switching_ and visual.pdf"
    name = safe_attachment_filename(raw, "application/pdf")
    assert name.endswith(".pdf")
    assert "/" not in name
