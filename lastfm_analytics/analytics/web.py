import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, abort, jsonify, render_template, request

from . import __version__, insights
from .db import Database
from .demo import seed
from .sync import SyncWorker

DEFAULTS = {
    "username": "",
    "api_key": "",
    "timezone": "Europe/London",
    "sync_interval_seconds": 300,
    "reconcile_days": 7,
    "demo_mode": False,
    "source_api_token": "",
}


def load_config(data_dir):
    path = Path(data_dir) / "options.json"
    config = {**DEFAULTS, **(json.loads(path.read_text()) if path.exists() else {})}
    config["username"] = str(config["username"]).strip()
    config["api_key"] = str(config["api_key"]).strip()
    if len(config["username"]) > 128 or len(config["api_key"]) > 256:
        raise ValueError("Username or API key is too long")
    for key, low, high in [
        ("sync_interval_seconds", 60, 86400),
        ("reconcile_days", 2, 90),
    ]:
        config[key] = int(config[key])
        if not low <= config[key] <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
    try:
        ZoneInfo(config["timezone"])
    except (ZoneInfoNotFoundError, TypeError):
        raise ValueError("Use an IANA timezone such as Europe/London") from None
    if not isinstance(config["demo_mode"], bool):
        raise ValueError("demo_mode must be true or false")
    return config


def create_app(data_dir="/data", config=None, development=False, start_worker=True):
    config = {**DEFAULTS, **config} if config is not None else load_config(data_dir)
    tz = ZoneInfo(config["timezone"])
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 65536
    csrf = secrets.token_urlsafe(32)
    account = hashlib.sha256(config["username"].casefold().encode()).hexdigest()[:24]
    database = Database(Path(data_dir) / f"listening-{account}.sqlite3")
    worker = SyncWorker(database, config)
    app.extensions["database"] = database
    app.extensions["sync_worker"] = worker
    demo_lock = threading.Lock()
    demo_db = None

    def is_demo():
        return config["demo_mode"] or request.args.get("demo") == "1"

    def db_for_request():
        nonlocal demo_db
        if is_demo():
            with demo_lock:
                if demo_db is None:
                    demo_db = Database(Path(data_dir) / "demo.sqlite3")
                    seed(demo_db)
            return demo_db
        return database

    def dates(conn):
        earliest = conn.execute(
            "SELECT MIN(ts) FROM scrobbles WHERE active=1"
        ).fetchone()[0]
        result = insights.period(request.args, tz, earliest=earliest)
        result["compare"] = result["compare"] and bool(
            Database.get(conn, "import", {}).get("complete")
        )
        return result

    @app.before_request
    def access():
        # Health carries no private data and must also be reachable by Supervisor.
        if request.path == "/health":
            return None
        if request.path == "/api/source-reports":
            expected = config["source_api_token"]
            supplied = request.headers.get("Authorization", "")
            if not expected or not secrets.compare_digest(supplied, "Bearer " + expected):
                abort(403)
            if request.method != "POST" or not request.is_json or config["demo_mode"]:
                abort(403)
            return None
        allowed = ("127.0.0.1", "::1") if development else ("172.30.32.2",)
        if request.remote_addr not in allowed:
            abort(403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            token = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(token, csrf) or not request.is_json:
                abort(403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https://lastfm.freetls.fastly.net https://lastfm-img2.akamaized.net; connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
        )
        return response

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(500)
    def failed(exc):
        return jsonify(error="The request could not finish. Please try again."), 500

    @app.get("/health")
    def health():
        with database.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify(status="ok", version=__version__)

    @app.get("/")
    def index():
        prefix = request.headers.get("X-Ingress-Path", "") if not development else ""
        if prefix and not re.fullmatch(r"/[A-Za-z0-9_/-]+", prefix):
            abort(400)
        if "//" in prefix:
            abort(400)
        return render_template("index.html", base=prefix.rstrip("/") + "/", csrf=csrf)

    @app.post("/api/source-reports")
    def source_report():
        if not config["username"]:
            raise ValueError("Configure a Last.fm username before receiving source reports")
        database.record_source(config["username"], request.get_json())
        return jsonify(ok=True)

    @app.get("/api/status")
    def status():
        db = db_for_request()
        with db.connect() as conn:
            counts = dict(
                conn.execute(
                    'SELECT COUNT(*) plays,MIN(ts) earliest,MAX(ts) latest,SUM(album="") missing_albums FROM scrobbles WHERE active=1'
                ).fetchone()
            )
            imp = db.get(conn, "import", {})
            playing = db.get(conn, "now_playing", {})
            if (
                time.time() - playing.get("checked_at", 0)
                > config["sync_interval_seconds"] * 2
            ):
                playing["track"] = None
            events = [
                dict(r)
                for r in conn.execute(
                    "SELECT id,ts,description,undone FROM grouping_events ORDER BY id DESC LIMIT 10"
                )
            ]
            removed = conn.execute(
                "SELECT COUNT(*) FROM scrobbles WHERE active=0"
            ).fetchone()[0]
            return jsonify(
                version=__version__,
                source_reporting_enabled=bool(config["source_api_token"]),
                source_reports=conn.execute("SELECT COUNT(*) FROM source_reports").fetchone()[0],
                demo=is_demo(),
                configured=bool(config["username"] and config["api_key"]),
                username="Demo listener" if is_demo() else config["username"],
                timezone=str(tz),
                counts=counts,
                sync={"phase": "demo", "error": None} if is_demo() else worker.status(),
                import_state=imp,
                last_sync=db.get(conn, "last_sync"),
                now_playing=playing,
                events=events,
                removed=removed,
                interval=config["sync_interval_seconds"],
                reconcile_days=config["reconcile_days"],
            )

    @app.get("/api/overview")
    def overview():
        return jsonify(
            insights.overview(db_for_request(), request.args, config["timezone"])
        )

    @app.get("/api/rankings")
    def ranking():
        with db_for_request().connect() as conn:
            conn.execute("BEGIN")
            p = dates(conn)
            offset = max(0, int(request.args.get("offset", 0)))
            extra, params = insights.scope(request.args)
            rows = insights.rankings(
                conn,
                p,
                request.args.get("kind", "song"),
                request.args.get("mode") == "raw",
                request.args.get("q", "")[:200],
                50,
                offset,
                extra,
                params,
            )
            return jsonify(
                rows=rows,
                total=rows[0]["total_rows"] if rows else 0,
                offset=offset,
                period=p,
            )

    @app.get("/api/history")
    def history():
        with db_for_request().connect() as conn:
            conn.execute("BEGIN")
            return jsonify(insights.history(conn, dates(conn), request.args, tz))

    @app.get("/api/detail")
    def detail():
        with db_for_request().connect() as conn:
            conn.execute("BEGIN")
            return jsonify(
                insights.details(
                    conn,
                    request.args.get("entity"),
                    request.args.get("id"),
                    request.args.get("mode") == "raw",
                    request.args,
                )
            )

    @app.post("/api/grouping")
    def grouping():
        data = request.get_json()
        if not isinstance(data, dict) or not isinstance(data.get("ids", []), list):
            raise ValueError("Invalid grouping request")
        db = db_for_request()
        if data.get("action") == "undo":
            db.undo_grouping()
        else:
            db.change_groups(data.get("action"), data.get("ids", []))
        return jsonify(ok=True)

    @app.post("/api/sync")
    def sync():
        if is_demo() or not config["username"] or not config["api_key"]:
            raise ValueError("Enter your Last.fm details in add-on configuration first")
        worker.wake.set()
        return jsonify(
            ok=True,
            message="Sync requested. Requests are paced to protect your API quota.",
        )

    if start_worker:
        worker.start()
    return app
