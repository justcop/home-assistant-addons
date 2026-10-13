"""Back-cover lookups must never replace a front cover with unrelated artwork."""
import io
from unittest.mock import Mock

import requests
from PIL import Image

from conftest import ALBUM, RELEASE


OTHER_RELEASE = 'd315b73b-6297-4afb-866d-72606e9831e0'


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (20, 20), 'navy').save(buffer, format='PNG')
    return buffer.getvalue()


def no_cover():
    response = requests.Response()
    response.status_code = 404
    raise requests.HTTPError('not found', response=response)


def test_back_cover_is_cached_and_resized_separately(application, client, monkeypatch):
    store = application.extensions['store']
    artwork = application.extensions['artwork']
    store.shelf(ALBUM, True)
    download = Mock(return_value=(png(), 'image/png'))
    monkeypatch.setattr(artwork, 'download', download)
    path = f'/api/albums/{ALBUM}/artwork/back?size=320'
    first = client.get(path)
    second = client.get(path)
    assert first.status_code == second.status_code == 200
    assert first.mimetype == 'image/webp'
    assert first.data == second.data
    assert first.headers['X-Artwork-Source'] == 'cover-art-archive'
    assert first.headers['Cache-Control'] == 'private, no-cache'
    assert download.call_count == 1
    assert f'/release/{RELEASE}/back-1200' in download.call_args.args[0]
    assert (artwork.directory / (ALBUM + '-back.img')).exists()
    assert not (artwork.directory / (ALBUM + '.img')).exists()
    assert store.cache_get('artwork:' + ALBUM + ':back')


def test_back_cover_falls_back_to_canonical_release_not_release_group(application, client, monkeypatch):
    artwork = application.extensions['artwork']
    store = application.extensions['store']
    store.set_setting('artwork_release:' + ALBUM, OTHER_RELEASE)
    urls = []

    def download(url):
        urls.append(url)
        if OTHER_RELEASE in url:
            no_cover()
        return png(), 'image/png'

    monkeypatch.setattr(artwork, 'download', download)
    response = client.get(f'/api/albums/{ALBUM}/artwork/back')
    assert response.status_code == 200
    assert [OTHER_RELEASE, OTHER_RELEASE, RELEASE] == [url.split('/release/')[1].split('/')[0] for url in urls]
    assert all('/release-group/' not in url and '/back-' in url for url in urls)


def test_missing_back_cover_returns_404_not_front_placeholder(application, client, monkeypatch):
    artwork = application.extensions['artwork']
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    download = Mock(side_effect=lambda url: no_cover())
    monkeypatch.setattr(artwork, 'download', download)
    path = f'/api/albums/{ALBUM}/artwork/back?size=640'
    first = client.get(path)
    second = client.get(path)
    assert first.status_code == second.status_code == 404
    assert download.call_count == 2  # The 1200px and 500px sizes were both absent.
    assert not (artwork.directory / (ALBUM + '-back.img')).exists()
    assert not store.cache_get('artwork:' + ALBUM + ':back')


def test_back_cover_store_preview_is_never_persisted(application, client, monkeypatch):
    artwork = application.extensions['artwork']
    store = application.extensions['store']
    monkeypatch.setattr(artwork, 'download', lambda url: (png(), 'image/png'))
    assert client.get(f'/api/albums/{ALBUM}/artwork/back?size=640').status_code == 200
    assert not list(artwork.directory.glob(ALBUM + '-back*'))
    assert not store.cache_get('artwork:' + ALBUM + ':back')



def test_back_falls_back_to_500px_if_1200px_is_unavailable(application, client, monkeypatch):
    artwork = application.extensions['artwork']
    urls = []

    def download(url):
        urls.append(url)
        if url.endswith('back-1200'):
            no_cover()
        return png(), 'image/png'

    monkeypatch.setattr(artwork, 'download', download)
    assert client.get(f'/api/albums/{ALBUM}/artwork/back').status_code == 200
    assert urls == [f'https://coverartarchive.org/release/{RELEASE}/back-1200',
                    f'https://coverartarchive.org/release/{RELEASE}/back-500']


def test_missing_back_does_not_cache_store_preview(application, client, monkeypatch):
    artwork = application.extensions['artwork']
    store = application.extensions['store']
    monkeypatch.setattr(artwork, 'download', lambda url: no_cover())
    assert client.get(f'/api/albums/{ALBUM}/artwork/back').status_code == 404
    assert not store.cache_get('artwork:' + ALBUM + ':back')
    with store.cache_connect() as db:
        assert not db.execute("SELECT key FROM cache WHERE key LIKE ?",
                              ('artwork:' + ALBUM + ':back%',)).fetchone()


def test_back_cover_uses_same_account_and_size_guards(application, client, monkeypatch):
    path = f'/api/albums/{ALBUM}/artwork/back'
    assert client.get(path + '?size=999').status_code == 400
    assert client.get(path + '?account=another').status_code == 403
    assert not (application.extensions['artwork'].directory / (ALBUM + '-back.img')).exists()


def test_reset_clears_back_images_and_metadata(application, client, monkeypatch):
    artwork = application.extensions['artwork']
    store = application.extensions['store']
    store.shelf(ALBUM, True)
    monkeypatch.setattr(artwork, 'download', lambda url: (png(), 'image/png'))
    assert client.get(f'/api/albums/{ALBUM}/artwork/back?size=320').status_code == 200
    artwork.reset(ALBUM)
    assert not list(artwork.directory.glob(ALBUM + '-back*'))
    assert not store.cache_get('artwork:' + ALBUM + ':back')
