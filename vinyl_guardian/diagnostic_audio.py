"""Lossless diagnostic storage and legacy WAV compatibility."""
from contextlib import contextmanager
import os
import subprocess
import tempfile
import wave


def audio_name(metadata):
    return metadata.get('audio') or metadata.get('wav') or ''


def write_flac(path, pcm, rate, channels):
    """Preserve signed 16-bit PCM samples, channels and rate."""
    temporary = str(path) + '.tmp'
    try:
        subprocess.run([
            'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 's16le', '-ar', str(rate), '-ac', str(channels), '-i', 'pipe:0',
            '-c:a', 'flac', '-sample_fmt', 's16', '-f', 'flac', temporary,
        ], input=pcm, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            check=True, timeout=30)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


@contextmanager
def open_pcm(path):
    """Return a WAV-style reader for WAV or losslessly decoded FLAC."""
    if str(path).lower().endswith('.flac'):
        with tempfile.TemporaryDirectory(prefix='vinyl-audio-') as directory:
            decoded = os.path.join(directory, 'decoded.wav')
            subprocess.run([
                'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-i', str(path), '-c:a', 'pcm_s16le', decoded,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=60)
            with wave.open(decoded, 'rb') as reader:
                yield reader
    else:
        with wave.open(str(path), 'rb') as reader:
            yield reader
