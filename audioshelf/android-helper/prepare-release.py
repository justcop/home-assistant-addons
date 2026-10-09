"""Prepare a stable APK name and bounded metadata from the CI-signed artifact."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

directory = Path(sys.argv[1])
source = directory / 'app/build/outputs/apk/debug/app-debug.apk'
gradle = (Path(__file__).parent / 'app/build.gradle').read_text()
code = int(re.search(r'versionCode (\d+)', gradle).group(1))
name = re.search(r"versionName '([0-9.]+)'", gradle).group(1)
if not (directory / 'signing-fingerprint.txt').read_text().strip():
    raise SystemExit('Missing signing fingerprint')
apk = directory / 'AudioShelf-Helper.apk'
shutil.copyfile(source, apk)
data = apk.read_bytes()
if not 0 < len(data) <= 40 * 1024 * 1024:
    raise SystemExit('Unexpected APK size')
(directory / 'audioshelf-helper-update.json').write_text(json.dumps({
    'schema': 1,
    'packageName': 'uk.co.justcop.audioshelf.helper',
    'versionCode': code,
    'versionName': name,
    'apkUrl': 'https://github.com/justcop/home-assistant-addons/releases/download/audioshelf-helper/AudioShelf-Helper.apk',
    'size': len(data),
    'sha256': hashlib.sha256(data).hexdigest(),
}, indent=2) + '\n')
