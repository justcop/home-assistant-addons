"""Generate or verify the reviewed test inventory, without executing the tests."""
import argparse
import contextlib
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'audioshelf'
INVENTORY = DIRECTORY / 'TEST_INVENTORY.md'


class Collection:
    def __init__(self):
        self.names = []

    def pytest_collection_modifyitems(self, items):
        self.names = sorted(item.nodeid for item in items)


def snapshot():
    collector = Collection()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = pytest.main([str(DIRECTORY / 'tests'), '--collect-only', '-q', '--rootdir', str(ROOT)], plugins=[collector])
    if result != 0:
        raise RuntimeError('Test collection failed. Run pytest --collect-only to inspect the error.')
    sources = sorted((DIRECTORY / 'tests').glob('*.py')) + sorted((DIRECTORY / 'tests').glob('*.cjs'))
    sources += [ROOT / '.github/workflows/audioshelf-tests.yml', Path(__file__).resolve()]
    digest = hashlib.sha256()
    for path in sorted(sources):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return collector.names, digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update', action='store_true')
    parser.add_argument('--review-note', help='Record the actual review, including any remaining limitations.')
    args = parser.parse_args()
    names, fingerprint = snapshot()
    cases = '\n'.join(f'{n}. `{name}`' for n, name in enumerate(names, 1))
    if args.update:
        if not args.review_note or not args.review_note.strip():
            parser.error('--update requires --review-note describing the review performed')
        stamp = datetime.now(ZoneInfo('Europe/London')).isoformat(timespec='seconds')
        metadata = {'reviewed_at': stamp, 'review_note': args.review_note.strip(), 'source_sha256': fingerprint}
        INVENTORY.write_text('# AudioShelf test inventory\n\nReviewed: '+stamp+' (Europe/London).\n\n'
            +args.review_note.strip()+'\n\n<!-- inventory: '+json.dumps(metadata)+' -->\n\n'
            +f'## Backend cases ({len(names)})\n\n'+cases+'\n\n'
            +'## Other release checks\n\n1. Browser flows: `tests/test_ui.cjs`, `tests/test_vinyl_ui.cjs` and `tests/test_accounts_ui.cjs`.\n'
            +'2. Python compilation and launcher shell syntax.\n3. Browser JavaScript syntax.\n'
            +'4. Add-on container build.\n5. Test inventory freshness, including changed test bodies and CI configuration.\n\n'
            +'CI verifies completeness and freshness, not whether a human judgement about redundancy is correct. '
            +'Review changed tests for duplicate coverage, obsolete requirements and meaningful regressions before updating this list. '
            +'Git history retains previous numbered lists, timestamps and review notes.\n')
        print(f'Updated {INVENTORY.relative_to(ROOT)}: {len(names)} backend cases.')
        return
    text = INVENTORY.read_text() if INVENTORY.exists() else ''
    try:
        metadata = json.loads(text.split('<!-- inventory: ', 1)[1].split(' -->', 1)[0])
        stamp = datetime.fromisoformat(metadata['reviewed_at'])
        valid = stamp.tzinfo is not None and bool(metadata['review_note'].strip())
    except (ValueError, IndexError, KeyError, TypeError):
        valid = False
        metadata = {}
    if not valid or metadata.get('source_sha256') != fingerprint or '\n'+cases+'\n' not in text:
        parser.exit(1, 'Test inventory is stale or unreviewed. Review changed tests, then run test_inventory.py --update --review-note "Describe your review" and commit TEST_INVENTORY.md.\n')
    print(f'Test inventory matches {len(names)} backend cases and reviewed test/CI sources.')


if __name__ == '__main__':
    main()
