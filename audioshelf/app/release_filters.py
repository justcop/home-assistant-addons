"""Persistent preferences for MusicBrainz editions, independent of Spotify playback."""
import re

from .errors import AppError

DEFAULT_FILTERS = {'countries': ['GB', 'US', 'XW', 'XE'], 'formats': ['vinyl', 'cd', 'digital'], 'strict_countries': False}
FORMAT_LABELS = {'vinyl': 'Vinyl', 'cd': 'CD', 'digital': 'Digital',
                 'cassette': 'Cassette', 'other': 'Other audio formats'}
VIDEO_FORMATS = {'DVD', 'DVD-Video', 'Blu-ray', 'VHS', 'VCD', 'Video'}


def validate_filters(value):
    if not isinstance(value, dict):
        raise AppError('Supply release countries and formats.')
    countries, formats = value.get('countries'), value.get('formats')
    if not isinstance(countries, list) or len(countries) > 250 or any(
            not isinstance(c, str) or not re.fullmatch(r'[A-Za-z]{2}', c) for c in countries):
        raise AppError('Use two-letter country codes, such as GB, US, XW or XE.')
    if not isinstance(formats, list) or not formats or any(
            not isinstance(f, str) or f not in FORMAT_LABELS for f in formats):
        raise AppError('Choose at least one supported audio format.')
    strict = value.get('strict_countries', False)
    if not isinstance(strict, bool) or (strict and not countries):
        raise AppError('Select countries before restricting releases to them.')
    return {'countries': list(dict.fromkeys(c.upper() for c in countries)),
            'formats': list(dict.fromkeys(formats)), 'strict_countries': strict}


def format_family(value):
    value = (value or '').lower()
    if 'vinyl' in value:
        return 'vinyl'
    if re.search(r'\bcd\b', value) or value in {'enhanced cd', 'hdcd'}:
        return 'cd'
    if value == 'digital media':
        return 'digital'
    if value == 'cassette':
        return 'cassette'
    return 'other'


def matches_filters(release, filters):
    countries = {release.get('country')}
    for event in release.get('release-events', []):
        countries.update((event.get('area') or {}).get('iso-3166-1-codes', []))
    if filters.get('strict_countries') and not countries.intersection(filters['countries']):
        return False
    media = [m for m in release.get('media', []) if m.get('format') not in VIDEO_FORMATS]
    return bool(media) and all(format_family(m.get('format')) in filters['formats'] for m in media)


def filter_description(filters):
    countries = ', '.join(filters['countries']) or 'all countries'
    return countries + ' · ' + ', '.join(FORMAT_LABELS[f] for f in filters['formats'])


def preference_rank(release, filters):
    countries = {release.get('country')}
    for event in release.get('release-events', []):
        countries.update((event.get('area') or {}).get('iso-3166-1-codes', []))
    country_rank = min((n for n, country in enumerate(filters['countries']) if country in countries), default=len(filters['countries']))
    media = [format_family(m.get('format')) for m in release.get('media', []) if m.get('format') not in VIDEO_FORMATS]
    format_rank = max((filters['formats'].index(f) if f in filters['formats'] else len(filters['formats']) for f in media), default=len(filters['formats']))
    return country_rank, format_rank
