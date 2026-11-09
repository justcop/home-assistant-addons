"""Run inside a built add-on image, without credentials or external requests."""
import importlib
import os
import subprocess
import sys

sys.path.insert(0, os.getcwd())
addon = sys.argv[1]
assert sys.version_info[:3] == (3, 15, 2), sys.version
modules = {
    'python': ['ssl', 'sqlite3', 'venv', 'ensurepip'],
    'audioshelf': ['flask', 'requests', 'waitress', 'PIL.Image', 'qrcode', 'app'],
    'lastfm_analytics': ['flask', 'waitress', 'PIL.Image', 'analytics'],
    'money_locations': ['sqlite3', 'ssl', 'zoneinfo', 'server'],
    'music_video_companion': ['websocket', 'ssl', 'server'],
    'work_audit': ['flask', 'paho.mqtt.client', 'websocket', 'app'],
    'vinyl_guardian': ['audioop', 'numpy', 'alsaaudio', 'paho.mqtt.client', 'requests', 'pylast', 'shazamio'],
}[addon]
for name in modules:
    print(f'Checking {name} in a fresh process', flush=True)
    subprocess.run([sys.executable, '-X', 'faulthandler', '-u', '-c',
                    'import importlib; importlib.import_module(' + repr(name) + ')'], check=True)
# Check imports together too, since extension load order can matter.
for name in modules:
    importlib.import_module(name)

if addon == 'python':
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run([sys.executable, '-m', 'venv', directory], check=True)
        subprocess.run([str(Path(directory) / 'bin/python'), '-c',
                        'import sys; assert sys.version_info[:3] == (3, 15, 2)'], check=True)

if addon in ('audioshelf', 'lastfm_analytics'):
    import io
    from PIL import Image, ImageOps
    for fmt in ('PNG', 'JPEG', 'WEBP'):
        buf = io.BytesIO()
        Image.new('RGB', (128, 64), 'blue').save(buf, format=fmt)
        with Image.open(io.BytesIO(buf.getvalue())) as original:
            image = ImageOps.exif_transpose(original)
            image.thumbnail((32, 32), Image.Resampling.LANCZOS)
            assert image.size == (32, 16)
            image.save(io.BytesIO(), format='JPEG')

if addon == 'vinyl_guardian':
    import asyncio
    import inspect
    import math
    import socket
    import struct
    import tempfile
    import wave
    from shazamio import Shazam
    import paho.mqtt.client as mqtt
    # Fail closed if a future library path attempts a network request.
    def deny_network(*args, **kwargs):
        raise AssertionError('Network is forbidden in the runtime smoke test')
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    class OfflineShazam(Shazam):
        async def send_recognize_request_v2(self, sig, proxy=None):
            assert sig is not None
            self.signature_seen = True
            return {'matches': [{'offset': 2}], 'track': {'title': 'Fixture', 'subtitle': 'Test', 'sections': []}}
    async def recognize(path):
        client = OfflineShazam()
        try:
            result = await client.recognize(path)
            assert result['track']['title'] == 'Fixture'
            assert client.signature_seen
        finally:
            close = getattr(client, 'close', None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
    with tempfile.NamedTemporaryFile(suffix='.wav') as audio:
        with wave.open(audio.name, 'wb') as wav:
            wav.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            wav.writeframes(b''.join(struct.pack('<h', int(12000 * math.sin(2 * math.pi * 440 * i / 44100)))
                                     for i in range(44100 * 10)))
        asyncio.run(recognize(audio.name))
        # Execute the production response parser without importing hardware/config globals.
        import ast
        import time
        from pathlib import Path
        tree = ast.parse(Path('/usr/src/app/integrations.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'recognize_shazam')
        env = dict(Shazam=OfflineShazam, asyncio=asyncio, inspect=inspect, os=os,
                   time=time, DEBUG=False, log=print)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'integrations', 'exec'), env)
        result = env['recognize_shazam'](audio.name)
        assert result['title'] == 'Fixture' and result['artist'] == 'Test'
        assert result['offset_seconds'] == 2
    mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    import alsaaudio
    pcm = alsaaudio.PCM(type=alsaaudio.PCM_PLAYBACK, mode=alsaaudio.PCM_NORMAL, device='null')
    try:
        pcm.setchannels(1)
        pcm.setrate(44100)
        pcm.setformat(alsaaudio.PCM_FORMAT_S16_LE)
        pcm.write(b'\0' * 2048)
    finally:
        pcm.close()
    print('Real decoder and native fingerprint completed offline', flush=True)
print(f'{addon}: Python {sys.version.split()[0]} runtime OK', flush=True)
