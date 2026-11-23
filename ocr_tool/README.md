# Home Assistant OCR Tool

A local Home Assistant add-on for OCR of screenshots, photos and PDFs.

## What it does

* Upload PDF, PNG, JPG, JPEG, WEBP or TIFF.
* Extracts embedded text from normal PDFs without OCR where possible.
* OCRs scanned/image-only PDF pages.
* OCRs uploaded images with Tesseract.
* Copy the result or download it as a text file.
* Everything is processed locally in the Home Assistant add-on container.

## Installation

This is intended initially as a local add-on.

1. Extract the `ocr_tool` folder into your Home Assistant `/addons/local/` directory.
2. In Home Assistant, go to **Settings > Apps** (called **Add-ons** on older versions).
3. Open the local apps/add-ons store and reload it if necessary.
4. Install **OCR Tool**.
5. Start it.
6. Open **OCR** from the Home Assistant sidebar.

The first build can take a while because the container installs Tesseract, Poppler and Python dependencies.

## Notes

The add-on currently supports amd64, aarch64 and armv7 at the configuration level. If a particular Home Assistant hardware platform cannot build one of the Python dependencies, remove that architecture from `config.yaml`.

The initial version deliberately uses Tesseract rather than a large neural OCR stack so that it remains practical on common Home Assistant hardware. It can be upgraded to PaddleOCR later if higher recognition accuracy is needed.

