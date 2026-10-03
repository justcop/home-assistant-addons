"""Reproducible cover downloads stay outside the collection; uploaded covers are durable."""
import base64
import binascii
import io
import logging
import os
import threading
import time
import uuid
import warnings
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, UnidentifiedImageError

from .errors import AppError

LOG = logging.getLogger('audioshelf')
MAX_BYTES = 5 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 16000000
PLACEHOLDER = b'<svg xmlns="http://www.w3.org/2000/svg" width="500" height="500"><rect width="500" height="500" fill="#e2dece"/><circle cx="250" cy="250" r="170" fill="#282b27"/><circle cx="250" cy="250" r="50" fill="#c9b883"/></svg>'


def image_type(data):
    if len(data) > MAX_BYTES:
        raise AppError('Choose an image smaller than 5 MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
        with image:
            if image.format not in {'JPEG','PNG','WEBP'}:
                raise ValueError()
            if image.width * image.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError()
            mime = Image.MIME[image.format]
            image.verify()
        return mime
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AppError('Choose a valid JPEG, PNG or WebP image, no larger than 16 megapixels.') from None


def allowed_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password and parsed.port in {None,443}
            and (host == 'coverartarchive.org' or host == 'archive.org' or host.endswith('.archive.org')
                 or host == 'i.scdn.co' or host.endswith('.scdn.co')))


class Artwork:
    def __init__(self, store, spotify, musicbrainz=None):
        self.store, self.spotify = store, spotify
        self.musicbrainz = musicbrainz
        self.directory = store.cache_directory / 'artwork'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.custom_directory = store.directory / 'custom-artwork'
        self.custom_directory.mkdir(exist_ok=True)
        self.locks = [threading.RLock() for _ in range(32)]
        self.download_slots = threading.Semaphore(3)
        self.prune_lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'AudioShelf/0.3.1 (https://github.com/justcop/home-assistant-addons)'

    def download(self, url):
        # Follow only known artwork hosts, including Cover Art Archive's Internet Archive redirects.
        for _ in range(6):
            if not allowed_url(url):
                raise AppError('Artwork source returned an unsupported image address.',502)
            with self.session.get(url, timeout=(3,8), stream=True, allow_redirects=False) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    url = urljoin(url,response.headers.get('Location',''))
                    continue
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data)>MAX_BYTES:
                        raise AppError('Artwork download exceeded 5 MB.',502)
                data = bytes(data)
                return data, image_type(data)
        raise AppError('Artwork source redirected too many times.',502)

    def _spotify_url(self, album):
        if not self.spotify.connected:
            return None
        if album.get('spotify_album_id'):
            source = self.spotify.album(album['spotify_album_id'])
        else:
            artist = album['artists'][0]['name'] if album['artists'] else ''
            query = f'album:"{album["title"].replace(chr(34),"")}" artist:"{artist.replace(chr(34),"")}"'
            result = self.spotify.api('GET','search',{'q':query,'type':'album','limit':10,'market':self.spotify.market})
            sources = result.get('albums',{}).get('items',[])
            if not sources:
                return None
            source = sources[0]
        images = sorted(source.get('images',[]),key=lambda image:abs((image.get('width') or 500)-500))
        return images[0]['url'] if images else None

    def _prune(self):
        with self.prune_lock:
            files = []
            for path in self.directory.glob('*.img'):
                try:
                    files.append((path,path.stat()))
                except FileNotFoundError:
                    pass
            total = sum(stat.st_size for _,stat in files)
            for path,stat in sorted(files,key=lambda item:item[1].st_mtime):
                if total <= 512 * 1024 * 1024:
                    break
                path.unlink(missing_ok=True)
                total -= stat.st_size

    def get(self, album_id):
        album = self.store.album(album_id)
        with self.locks[hash(album_id)%len(self.locks)]:
            override = self.store.artwork_override(album_id)
            if override:
                path = self.custom_directory / Path(override).name
                if path.exists():
                    data = path.read_bytes()
                    return data, image_type(data), 'custom'
            path = self.directory / (album_id+'.img')
            key = 'artwork:'+album_id
            metadata = self.store.cache_get(key)
            chosen_release = self.store.setting('artwork_release:'+album_id)
            identity = {'release': album.get('release_id'), 'chosen': chosen_release}
            if metadata and metadata.get('identity') == identity and path.exists():
                try:
                    data = path.read_bytes()
                    return data, image_type(data), metadata['source']
                except (AppError, OSError):
                    path.unlink(missing_ok=True)
            # A later Spotify connection/mapping must be able to resolve an earlier missing cover.
            negative = key+':missing:'+str(identity)+':'+str(self.spotify.connected)+':'+str(album.get('spotify_album_id'))
            if self.store.cache_get(negative):
                return PLACEHOLDER, 'image/svg+xml', 'placeholder'
            with self.download_slots:
                urls = []
                cover_release = album.get('release_id')
                if ('cassette' in (album.get('release_label') or '').lower()
                        and 'cassette' not in self.store.release_filters(album_id)['formats']):
                    cover_release = None
                    if self.musicbrainz and not chosen_release:
                        try:
                            page = self.musicbrainz.release_page(album_id)
                            if page['releases']:
                                cover_release = page['releases'][0]['id']
                        except AppError:
                            pass
                for release_id in dict.fromkeys([chosen_release, cover_release]):
                    if release_id:
                        urls.append(('cover-art-archive',f'https://coverartarchive.org/release/{release_id}/front-500'))
                urls.append(('cover-art-archive',f'https://coverartarchive.org/release-group/{album_id}/front-500'))
                for source,url in urls + [('spotify',None)]:
                    try:
                        url = url or self._spotify_url(album)
                        if not url:
                            continue
                        data,mime = self.download(url)
                        temporary = path.with_suffix('.tmp')
                        temporary.write_bytes(data)
                        os.replace(temporary,path)
                        self.store.cache_put(key,{'source':source, 'identity':identity, 'url':url},ttl=86400 if source=='spotify' else 30*86400)
                        self._prune()
                        return data,mime,source
                    except (requests.RequestException, AppError, OSError):
                        LOG.debug('Artwork source unavailable for %s (%s)',album_id,source)
                self.store.cache_put(negative,True,ttl=300)
                return PLACEHOLDER, 'image/svg+xml', 'placeholder'

    def upload(self, album_id, encoded):
        self.store.album(album_id)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise AppError('Choose a valid image file.') from None
        image_type(data)
        with self.locks[hash(album_id)%len(self.locks)]:
            filename = album_id+'-'+uuid.uuid4().hex+'.img'
            path = self.custom_directory/filename
            path.write_bytes(data)
            previous = self.store.artwork_override(album_id)
            self.store.set_artwork_override(album_id,filename)
            if previous:
                (self.custom_directory/Path(previous).name).unlink(missing_ok=True)

    def reset(self, album_id):
        self.store.album(album_id)
        with self.locks[hash(album_id)%len(self.locks)]:
            previous = self.store.artwork_override(album_id)
            self.store.set_artwork_override(album_id,None)
            self.store.set_setting('artwork_release:'+album_id, None)
            if previous:
                (self.custom_directory/Path(previous).name).unlink(missing_ok=True)
            (self.directory/(album_id+'.img')).unlink(missing_ok=True)
            with self.store.cache_connect() as db:
                db.execute('DELETE FROM cache WHERE key LIKE ?',('artwork:'+album_id+'%',))

    def choose_release(self, album_id, release_id):
        # A missing front cover cannot erase a working custom cover.
        data, mime = self.download(f'https://coverartarchive.org/release/{release_id}/front-500')
        with self.locks[hash(album_id)%len(self.locks)]:
            self.reset(album_id)
            self.store.set_setting('artwork_release:'+album_id, release_id)
            album = self.store.album(album_id)
            path = self.directory/(album_id+'.img')
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(data)
            os.replace(temporary, path)
            self.store.cache_put('artwork:'+album_id, {'source': 'cover-art-archive',
                'identity': {'release': album.get('release_id'), 'chosen': release_id},
                'url': f'https://coverartarchive.org/release/{release_id}/front-500'}, ttl=30*86400)
            self._prune()
        return mime
