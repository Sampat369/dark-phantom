from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree
def extract(path: str, mime: str):
    p=Path(path); ext=p.suffix.lower()
    if ext==".pdf":
        from pypdf import PdfReader
        return [(page.extract_text() or "", {"page":i+1}) for i,page in enumerate(PdfReader(p).pages)]
    if ext==".docx":
        with ZipFile(p) as archive:
            document = ElementTree.fromstring(archive.read("word/document.xml"))
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = ["".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)) for paragraph in document.findall(".//w:p", namespace)]
        return [("\n".join(paragraphs), {})]
    if ext in {".eml",".msg"}:
        from email import policy
        from email.parser import BytesParser
        msg=BytesParser(policy=policy.default).parsebytes(p.read_bytes()); body=msg.get_body(preferencelist=("plain",)); return [(f"From: {msg.get('From','')}\nTo: {msg.get('To','')}\nDate: {msg.get('Date','')}\nSubject: {msg.get('Subject','')}\n\n{body.get_content() if body else msg.get_payload()}", {"email_headers":dict(msg.items())})]
    if ext in {".png",".jpg",".jpeg"}:
        import pytesseract
        from PIL import Image
        return [(pytesseract.image_to_string(Image.open(p)), {})]
    return [(p.read_text(errors="replace"), {})]
