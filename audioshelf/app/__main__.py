import logging
import os

from waitress import serve

from .server import create_app

logging.basicConfig(level=logging.INFO,format='[%(asctime)s] [AudioShelf] %(message)s')
app = create_app()
logging.getLogger('audioshelf').info('Starting AudioShelf %s, library at %s',
                                  os.environ.get('AUDIOSHELF_VERSION','0.1.0'), app.extensions['store'].directory)
# Keep enough request workers for navigation and playback while the few
# allowed external artwork downloads are in flight. Idle keepalive channels
# should not consume the entire connection limit after a burst of covers.
serve(app,host='0.0.0.0',port=int(os.environ.get('AUDIOSHELF_PORT','8099')),threads=12,
      connection_limit=180,channel_timeout=60,cleanup_interval=15,
      ident='AudioShelf',max_request_body_size=7*1024*1024)
