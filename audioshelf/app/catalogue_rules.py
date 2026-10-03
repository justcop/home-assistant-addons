"""Small, sourced exceptions to generic MusicBrainz classifications, keyed by MBID.

The Beatles: https://musicbrainz.org/series/255a357a-909a-4437-9b7a-bfbb814bde77
Cross-check: https://www.thebeatles.com/albums (original 1963–1970 albums).
Past Masters and later compilations remain outside the studio-album catalogue.
"""
BEATLES = 'b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d'
BEATLES_SERIES = '255a357a-909a-4437-9b7a-bfbb814bde77'
MAGICAL_MYSTERY_TOUR = '6e514645-fbee-34ac-97e0-c2120a4a5644'
BEATLES_CORE = {
    'de208292-8db5-3aed-a14a-b37a84d8c521': 'Please Please Me',
    'a63dc65f-09f2-359b-a10e-648f00ecd96c': 'With The Beatles',
    '06281c4d-112d-33b0-a25b-df63b420eae7': 'A Hard Day’s Night',
    'f50a3b6f-27f0-3832-bd3f-3568dc557d95': 'Beatles for Sale',
    '0d44e1cb-c6e0-3453-8b68-4d2082f05421': 'Help!',
    'dca03435-8adb-30a5-ba82-5a162267ff38': 'Rubber Soul',
    '72d15666-99a7-321e-b1f3-a3f8c09dff9f': 'Revolver',
    '9f7a4c28-8fa2-3113-929c-c47a9f7982c3': 'Sgt. Pepper’s Lonely Hearts Club Band',
    MAGICAL_MYSTERY_TOUR: 'Magical Mystery Tour',
    '055be730-dcad-31bf-b550-45ba9c202aa3': 'The Beatles',
    '8d5f4b07-7e2e-4ffa-ac90-e4772c4d8525': 'Yellow Submarine',
    '9162580e-5df4-32de-80cc-f45a8d8a9b1d': 'Abbey Road',
    'bff544a7-56e0-3ed6-9e0f-3b676cca9111': 'Let It Be',
}


def is_beatles(group):
    return any(isinstance(c, dict) and c.get('artist', {}).get('id') == BEATLES
               for c in group.get('artist-credit', []))
