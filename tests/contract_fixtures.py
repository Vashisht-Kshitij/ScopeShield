"""Synthetic contract and PDF bytes used only by deterministic offline tests."""
from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.extraction import OfflineDemoProvider, SAMPLE_PATH


def sample_text():
    return SAMPLE_PATH.read_text(encoding="utf-8").strip()


def sample_output():
    import json
    return json.loads(OfflineDemoProvider().extract(sample_text()))


def pdf_bytes(text=None, *, pages=1, password=None):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        if text is not None:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            })
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            commands = ["BT /F1 10 Tf 14 TL 40 740 Td"]
            for line in text.splitlines():
                escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                commands.append(f"({escaped}) Tj T*")
            commands.append("ET")
            stream = DecodedStreamObject()
            stream.set_data("\n".join(commands).encode("latin-1"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class MockExtractionProvider:
    name = "mock_test_provider"
    live_ai = False

    def __init__(self, output):
        import json
        self.output = output if isinstance(output, str) else json.dumps(output)
        self.calls = []

    def extract(self, text):
        self.calls.append(text)
        return self.output
