"""Local HTTPS fixture for the browser authentication test, never a production entry point."""

import argparse
from werkzeug.serving import make_server
from analytics.web import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", required=True)
parser.add_argument("--certificate", required=True)
parser.add_argument("--key", required=True)
args = parser.parse_args()
app = create_app(
    args.data_dir,
    config={"web_password": "fictional-test-password", "demo_mode": True},
    start_worker=False,
)
make_server(
    "127.0.0.1", 8107, app, threaded=True, ssl_context=(args.certificate, args.key)
).serve_forever()
