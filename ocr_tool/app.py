import io
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from PIL import Image, ImageOps, ImageFilter

app = FastAPI(title="OCR Tool")

MAX_BYTES = 50 * 1024 * 1024

class CollapseSlashesMiddleware:
    """Home Assistant's ingress proxy forwards requests as
    ``ingress_entry`` + the browser's path, which yields a doubled
    leading slash (e.g. ``//`` or ``//ocr``) for every request. Collapse
    repeated slashes before routing so ingress traffic resolves to the
    same routes as direct traffic."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            collapsed = re.sub(r"/{2,}", "/", path)
            if collapsed != path:
                scope = dict(scope)
                scope["path"] = collapsed
        await self.app(scope, receive, send)

app.add_middleware(CollapseSlashesMiddleware)

HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OCR Tool</title>
<style>
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 0; background: #f5f5f5; color: #222; }
main { max-width: 900px; margin: auto; padding: 20px; }
.card { background: white; border-radius: 14px; padding: 20px; box-shadow: 0 2px 12px #0001; }
h1 { margin-top: 0; }
.drop { display: block; border: 2px dashed #888; border-radius: 12px; padding: 35px 15px; text-align: center; cursor: pointer; margin-bottom: 15px; }
.drop.drag { border-color: #1976d2; background: #e3f2fd; }
input[type=file] { display:none; }
button { border: 0; border-radius: 9px; padding: 11px 18px; font-size: 16px; cursor:pointer; }
.primary { background:#1976d2; color:white; }
.secondary { background:#ddd; color:#222; }
button:disabled { opacity:.5; }
textarea { width:100%; min-height:55vh; box-sizing:border-box; border:1px solid #bbb; border-radius:10px; padding:12px; font: 14px/1.45 ui-monospace, monospace; resize:vertical; }
#status { margin:12px 0; min-height:24px; }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin:12px 0; }
.small { color:#666; font-size:13px; }
@media (prefers-color-scheme: dark) {
  body { background:#111; color:#eee; }
  .card { background:#1d1d1d; }
  .drop.drag { background:#12304a; }
  .secondary { background:#444; color:#fff; }
  textarea { background:#111; color:#eee; border-color:#555; }
  .small { color:#aaa; }
}
</style>
</head>
<body>
<main>
<div class="card">
<h1>OCR Tool</h1>
<p class="small">Upload a screenshot, image, or PDF. Processing happens locally on Home Assistant.</p>

<label class="drop" id="drop">
  <strong>Tap to choose a file</strong><br>
  or drag and drop one here
  <input id="file" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff">
</label>

<div id="filename" class="small"></div>
<div class="actions">
  <button class="primary" id="ocr" disabled>OCR</button>
  <button class="secondary" id="copy" disabled>Copy text</button>
  <button class="secondary" id="download" disabled>Download .txt</button>
  <button class="secondary" id="clear">Clear</button>
</div>

<div id="status"></div>
<textarea id="result" placeholder="OCR text will appear here..." spellcheck="false"></textarea>
</div>
</main>
<script>
const fileInput = document.getElementById('file');
const drop = document.getElementById('drop');
const ocrBtn = document.getElementById('ocr');
const copyBtn = document.getElementById('copy');
const downloadBtn = document.getElementById('download');
const clearBtn = document.getElementById('clear');
const result = document.getElementById('result');
const status = document.getElementById('status');
const filename = document.getElementById('filename');
let selected = null;

function setFile(f) {
  selected = f;
  filename.textContent = f ? `${f.name} (${Math.round(f.size/1024)} KB)` : '';
  ocrBtn.disabled = !f;
  status.textContent = '';
}
drop.onclick = () => fileInput.click();
fileInput.onchange = e => setFile(e.target.files[0]);

drop.ondragover = e => { e.preventDefault(); drop.classList.add('drag'); };
drop.ondragleave = () => drop.classList.remove('drag');
drop.ondrop = e => { e.preventDefault(); drop.classList.remove('drag'); setFile(e.dataTransfer.files[0]); };

ocrBtn.onclick = async () => {
  if (!selected) return;
  ocrBtn.disabled = true;
  status.textContent = 'Processingâ¦ this may take a little while for a large PDF.';
  result.value = '';
  try {
    const fd = new FormData();
    fd.append('file', selected);
    const r = await fetch('./ocr', {method:'POST', body:fd});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'OCR failed');
    result.value = data.text || '';
    status.textContent = `Done. ${data.pages || 1} page(s) processed.`;
    copyBtn.disabled = !result.value;
    downloadBtn.disabled = !result.value;
  } catch (e) {
    status.textContent = 'Error: ' + e.message;
  } finally {
    ocrBtn.disabled = false;
  }
};

copyBtn.onclick = async () => {
  await navigator.clipboard.writeText(result.value);
  status.textContent = 'Copied to clipboard.';
};

downloadBtn.onclick = () => {
  const blob = new Blob([result.value], {type:'text/plain;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (selected?.name || 'ocr').replace(/\.[^.]+$/, '') + '.txt';
  a.click();
  URL.revokeObjectURL(a.href);
};

clearBtn.onclick = () => {
  selected = null;
  fileInput.value = '';
  filename.textContent = '';
  result.value = '';
  status.textContent = '';
  ocrBtn.disabled = true;
  copyBtn.disabled = true;
  downloadBtn.disabled = true;
};
</script>
</body>
</html>
"""

def clean_text(text: str) -> str:
    text = text.replace("\x0c", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def preprocess(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    # Upscaling helps screenshots and small printed text.
    if img.width < 1800:
        scale = min(2.0, 1800 / img.width)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    return img

def tesseract_image(img: Image.Image) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        preprocess(img).save(path, "PNG")
        p = subprocess.run(
            ["tesseract", path, "stdout", "--psm", "3"],
            capture_output=True, text=True, timeout=120
        )
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip() or "Tesseract failed")
        return p.stdout
    finally:
        try: os.unlink(path)
        except OSError: pass

def pdf_to_text(data: bytes):
    doc = pymupdf.open(stream=data, filetype="pdf")
    chunks = []
    ocr_pages = 0

    for i, page in enumerate(doc):
        existing = page.get_text("text").strip()
        # If there is meaningful embedded text, use it rather than OCR.
        if len(existing) >= 20:
            chunks.append(existing)
            continue

        pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        chunks.append(tesseract_image(img))
        ocr_pages += 1

    doc.close()
    return clean_text("\n\n".join(chunks)), len(chunks), ocr_pages

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML

@app.post("/ocr")
async def ocr(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is larger than the 50 MB limit.")

    name = (file.filename or "").lower()
    content_type = file.content_type or ""

    try:
        if name.endswith(".pdf") or content_type == "application/pdf":
            text, pages, ocr_pages = pdf_to_text(data)
        elif content_type.startswith("image/") or name.endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")
        ):
            img = Image.open(io.BytesIO(data))
            text = clean_text(tesseract_image(img))
            pages, ocr_pages = 1, 1
        else:
            raise HTTPException(400, "Unsupported file type. Use PDF, PNG, JPG, WEBP or TIFF.")

        return {"text": text, "pages": pages, "ocr_pages": ocr_pages}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"OCR failed: {exc}")


