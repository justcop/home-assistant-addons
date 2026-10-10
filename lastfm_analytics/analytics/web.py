from datetime import datetime
import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (
    Response,
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)
from werkzeug.security import check_password_hash, generate_password_hash

from . import __version__, insights
from .cache import ViewCache
from .review import review
from .grouping import normalise
from .db import Database
from .demo import seed
from .sync import SyncWorker
from .artwork import ArtworkWorker
from .album_listens import TracklistWorker

DEFAULTS = {
    "username": "",
    "api_key": "",
    "timezone": "Europe/London",
    "sync_interval_seconds": 300,
    "reconcile_days": 7,
    "demo_mode": False,
    "web_password": "",
    "source_api_token": "",
    "audioshelf_url": "",
    "artwork_lookups": True,
}


class ConfigurationError(ValueError):
    """A fixed, credential-free explanation suitable for startup logs."""


def validate_config(config):
    config = {**DEFAULTS, **config}
    for key in ("username", "api_key", "web_password", "source_api_token"):
        if config[key] is None:
            config[key] = ""
    config["username"] = str(config["username"]).strip()
    config["api_key"] = str(config["api_key"]).strip()
    if len(config["username"]) > 128 or len(config["api_key"]) > 256:
        raise ConfigurationError("Last.fm username or API key is too long")
    for key, low, high in [
        ("sync_interval_seconds", 60, 86400),
        ("reconcile_days", 2, 90),
    ]:
        try:
            config[key] = int(config[key])
        except (TypeError, ValueError, OverflowError):
            raise ConfigurationError(
                f"{key} must be a whole number between {low} and {high}"
            ) from None
        if not low <= config[key] <= high:
            raise ConfigurationError(f"{key} must be between {low} and {high}")
    try:
        ZoneInfo(config["timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ConfigurationError(
            "timezone must be an IANA timezone such as Europe/London"
        ) from None
    if not isinstance(config["artwork_lookups"], bool):
        raise ConfigurationError("artwork_lookups must be true or false")
    if not isinstance(config["demo_mode"], bool):
        raise ConfigurationError("demo_mode must be true or false")
    if not isinstance(config["web_password"], str):
        raise ConfigurationError(
            "web_password must be text, or blank to disable direct browser access"
        )
    if config["web_password"] and not 12 <= len(config["web_password"]) <= 256:
        raise ConfigurationError(
            "web_password must contain 12 to 256 characters, or be blank to disable direct browser access. Change Web login password in the Home Assistant add-on Configuration tab, save and restart."
        )
    if not isinstance(config["source_api_token"], str):
        raise ConfigurationError(
            "source_api_token must be text, or blank to disable source reports"
        )
    value = str(config.get("audioshelf_url") or "").strip().rstrip("/")
    if value:
        from urllib.parse import urlsplit
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c in value for c in "\\'\" \r\n;"):
            raise ConfigurationError("AudioShelf URL must be an HTTPS web address")
    config["audioshelf_url"] = value
    return config


def load_config(data_dir):
    path = Path(data_dir) / "options.json"
    try:
        options = json.loads(path.read_text()) if path.exists() else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ConfigurationError(
            "options.json is invalid. Save the add-on configuration again in Home Assistant."
        ) from None
    if not isinstance(options, dict):
        raise ConfigurationError(
            "options.json must contain an options object. Save the add-on configuration again in Home Assistant."
        )
    return validate_config(options)


def create_app(data_dir="/data", config=None, development=False, start_worker=True):
    config = validate_config(config) if config is not None else load_config(data_dir)
    tz = ZoneInfo(config["timezone"])
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 65536
    password = config["web_password"]
    secret_path = Path(data_dir) / "web-session-secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with secret_path.open("x") as handle:
            secret_path.chmod(0o600)
            handle.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    # Changing the configured password invalidates all existing sessions.
    app.secret_key = hashlib.sha256(
        (secret_path.read_text() + password).encode()
    ).digest()
    app.config.update(
        SESSION_COOKIE_NAME="listening_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=not development,
        SESSION_COOKIE_SAMESITE="Lax",
        # A slow background read must not reissue an authenticated cookie
        # after a later logout response has cleared it.
        SESSION_REFRESH_EACH_REQUEST=False,
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )
    password_hash = generate_password_hash(password) if password else None
    login_attempts = {}
    login_lock = threading.Lock()
    csrf = secrets.token_urlsafe(32)
    account = hashlib.sha256(config["username"].casefold().encode()).hexdigest()[:24]
    database = Database(Path(data_dir) / f"listening-{account}.sqlite3")
    worker = SyncWorker(database, config)
    app.extensions["database"] = database
    app.extensions["sync_worker"] = worker
    artwork_worker = ArtworkWorker(config["api_key"], enabled=start_worker and config["artwork_lookups"])
    app.extensions["artwork_worker"] = artwork_worker
    tracklist_worker = TracklistWorker(enabled=start_worker and not config["demo_mode"],
                                       api_key=config["api_key"])
    app.extensions["tracklist_worker"] = tracklist_worker

    def calculate_rankings(db, args):
        with db.connect() as conn:
            conn.execute("BEGIN")
            earliest = conn.execute(
                "SELECT MIN(ts) FROM scrobbles WHERE active=1"
            ).fetchone()[0]
            p = insights.period(args, tz, earliest=earliest)
            p["compare"] = p["compare"] and bool(
                db.get(conn, "import", {}).get("complete")
            )
            offset = max(0, int(args.get("offset", 0)))
            extra, params = insights.scope(args)
            rows = insights.rankings(
                conn,
                p,
                args.get("kind", "song"),
                args.get("mode") == "raw",
                args.get("q", "")[:200],
                50,
                offset,
                extra,
                params,
                sort=args.get("album_sort", "scrobbles") if args.get("album_sort") in ("scrobbles", "estimated") else "scrobbles",
            )
            return dict(
                rows=rows,
                total=rows[0]["total_rows"] if rows else 0,
                offset=offset,
                period=p,
            )

    view_cache = ViewCache(
        config["timezone"],
        {
            "overview": lambda db, args: insights.overview(
                db, args, config["timezone"]
            ),
            "rankings": calculate_rankings,
        },
    )
    app.extensions["view_cache"] = view_cache
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
        tracklist_worker.scan(database)
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

    def ingress_request():
        return not development and request.remote_addr == "172.30.32.2"

    @app.before_request
    def access():
        # Health carries no private data and must also be reachable by Supervisor.
        if request.path == "/health":
            return None
        if request.path == "/api/source-reports":
            expected = config["source_api_token"]
            supplied = request.headers.get("Authorization", "")
            if not expected or not secrets.compare_digest(
                supplied, "Bearer " + expected
            ):
                abort(403)
            if request.method != "POST" or not request.is_json or config["demo_mode"]:
                abort(403)
            return None
        ingress = ingress_request()
        local_dev = development and request.remote_addr in ("127.0.0.1", "::1")
        if not ingress and not local_dev and not password_hash:
            abort(403)
        if request.path == "/login" or request.path in (
            "/static/style.css",
            "/static/icon-192.png",
            "/static/icon-512.png",
            "/manifest.webmanifest",
            "/sw.js",
        ):
            return None
        if not ingress and not local_dev and not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify(error="Sign in to continue."), 401
            return redirect("/login")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            token = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(token, csrf) or not request.is_json:
                abort(403)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if ingress_request():
            return redirect(request_base())
        if not password_hash:
            abort(403)
        error = None
        code = 200
        if "login_csrf" not in session:
            session["login_csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST":
            if not secrets.compare_digest(
                request.form.get("csrf", ""), session["login_csrf"]
            ):
                abort(403)
            now = time.monotonic()
            with login_lock:
                # Ignore spoofable forwarded headers, bound memory and password work.
                for ip in list(login_attempts):
                    if now - login_attempts[ip][0] >= 600:
                        del login_attempts[ip]
                ip = request.remote_addr
                attempts = login_attempts.get(ip, [now, 0])
                limited = (
                    attempts[1] >= 10
                    or sum(x[1] for x in login_attempts.values()) >= 100
                )
                if not limited:
                    attempts[1] += 1
                    login_attempts[ip] = attempts
            if limited:
                error, code = "Too many attempts. Try again in ten minutes.", 429
            elif len(request.form.get("password", "")) <= 256 and check_password_hash(
                password_hash, request.form.get("password", "")
            ):
                session.clear()
                session["authenticated"] = True
                session.permanent = True
                return redirect("/")
            else:
                error, code = "Incorrect password.", 401
        response = app.make_response(
            (
                render_template("login.html", error=error, csrf=session["login_csrf"]),
                code,
            )
        )
        if code == 429:
            response.headers["Retry-After"] = "600"
        return response

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https://lastfm.freetls.fastly.net https://lastfm-img2.akamaized.net https://e-cdns-images.dzcdn.net https://www.theaudiodb.com https://theaudiodb.com https://r2.theaudiodb.com; connect-src 'self' {config['audioshelf_url']}; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
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

    def request_base():
        prefix = request.headers.get("X-Ingress-Path", "") if ingress_request() else ""
        if prefix and not re.fullmatch(r"/[A-Za-z0-9_/-]+", prefix):
            abort(400)
        if "//" in prefix:
            abort(400)
        return prefix.rstrip("/") + "/"

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            base=request_base(),
            csrf=csrf,
            direct_login=bool(password_hash) and not ingress_request(),
        )

    @app.get("/manifest.webmanifest")
    def manifest():
        base = request_base()
        response = jsonify(
            id=base,
            name="Listening Analytics",
            short_name="Listening",
            description="Explore your listening history and music statistics.",
            start_url=base,
            scope=base,
            display="standalone",
            background_color="#f5f6fa",
            theme_color="#5b5fe9",
            icons=[
                {
                    "src": base + f"static/icon-{size}.png",
                    "sizes": f"{size}x{size}",
                    "type": "image/png",
                    "purpose": "any maskable",
                }
                for size in (192, 512)
            ],
        )
        response.mimetype = "application/manifest+json"
        return response

    @app.get("/sw.js")
    def service_worker():
        # Network-only: private pages and API data never enter browser caches.
        return Response(
            """self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  if (event.request.mode === 'navigate') event.respondWith(fetch(event.request).catch(() =>
    new Response('<!doctype html><meta name="viewport" content="width=device-width"><title>Listening Analytics</title><h1>You are offline</h1><p>Reconnect to open your listening history.</p>', { headers: { 'Content-Type': 'text/html' } })));
});""",
            mimetype="application/javascript",
        )

    @app.post("/api/source-reports")
    def source_report():
        if not config["username"]:
            raise ValueError(
                "Configure a Last.fm username before receiving source reports"
            )
        database.record_source(config["username"], request.get_json())
        return jsonify(ok=True)

    @app.get("/api/status")
    def status():
        db = db_for_request()
        with db.connect() as conn:
            counts = dict(
                conn.execute(
                    "SELECT COUNT(*) plays,MIN(ts) earliest,MAX(ts) latest,SUM(album='') missing_albums FROM scrobbles WHERE active=1"
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
            years = []
            if counts["earliest"] is not None:
                first = datetime.fromtimestamp(counts["earliest"], tz).year
                last = datetime.fromtimestamp(counts["latest"], tz).year
                for year in range(last, first - 1, -1):
                    start = int(datetime(year, 1, 1, tzinfo=tz).timestamp())
                    end = int(datetime(year + 1, 1, 1, tzinfo=tz).timestamp())
                    if conn.execute(
                        "SELECT 1 FROM scrobbles WHERE active=1 AND ts>=? AND ts<? LIMIT 1",
                        (start, end),
                    ).fetchone():
                        years.append(year)
            return jsonify(
                years=years,
                version=__version__,
                audioshelf_url=config["audioshelf_url"],
                source_reporting_enabled=bool(config["source_api_token"]),
                source_reports=conn.execute(
                    "SELECT COUNT(*) FROM source_reports"
                ).fetchone()[0],
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
        return jsonify(view_cache.get(db_for_request(), "overview", dict(request.args)))

    @app.get("/api/rankings")
    def ranking():
        return jsonify(view_cache.get(db_for_request(), "rankings", dict(request.args)))

    @app.get("/api/history")
    def history():
        with db_for_request().connect() as conn:
            conn.execute("BEGIN")
            return jsonify(insights.history(conn, dates(conn), request.args, tz))

    def detail_artwork(db, result=None, album_info=None):
        if not config["artwork_lookups"]:
            return dict(artwork=None, artwork_pending=False, artist_photo=None, artist_logo=None)
        kind, value = request.args.get("entity"), request.args.get("id")
        raw = request.args.get("mode") == "raw"
        with db.connect() as conn:
            conn.execute("BEGIN")
            artwork = result.get("artwork") if result else insights.cover_art(
                conn, kind, value, raw, request.args)
            albums = (album_info[0] if album_info is not None else
                      insights.artwork_albums(conn, kind, value, raw, request.args)[0])
        if kind == "artist" and not is_demo():
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT display_name FROM artist_aliases WHERE artist_key=?", (value,)
                ).fetchone()
            if row:
                assets = artwork_worker.resolve_artist(db, row[0])
                if artwork:
                    return dict(artwork=artwork, **assets)
                fallback = artwork_worker.resolve(db, albums)
                assets["artwork_pending"] |= fallback["artwork_pending"]
                return dict(artwork=fallback["artwork"], **assets)
        if artwork:
            return dict(artwork=artwork, artwork_pending=False)
        if is_demo():
            return dict(artwork=None, artwork_pending=False)
        return artwork_worker.resolve(db, albums)

    @app.get("/api/artwork")
    def artwork():
        return jsonify(detail_artwork(db_for_request()))

    @app.get("/api/detail")
    def detail():
        db = db_for_request()
        with db.connect() as conn:
            conn.execute("BEGIN")
            kind, value = request.args.get("entity"), request.args.get("id")
            raw = request.args.get("mode") == "raw"
            result = insights.details(
                conn, kind, value, raw, request.args, include_artwork=False,
            )
            album_info = insights.artwork_albums(conn, kind, value, raw, request.args)
            result["listening_albums"] = [a["album"] for a in album_info[0]]
            if config["artwork_lookups"]:
                result["artwork"] = insights.cover_art(
                    conn, kind, value, raw, request.args, album_info=album_info,
                )
        result.update(detail_artwork(db, result, album_info))
        return jsonify(result)

    @app.get("/api/artists-review")
    def artists_review():
        db = db_for_request()
        needle = normalise(request.args.get("q", "")[:200])
        offset = max(0, int(request.args.get("offset", 0)))
        with db.connect() as conn:
            artists = [
                dict(r) for r in conn.execute(
                    """SELECT s.artist_group_key AS id, COUNT(*) AS plays,
                       COALESCE((SELECT display_name FROM artist_aliases
                       WHERE artist_key=s.artist_group_key), MIN(s.artist)) AS name,
                       COUNT(DISTINCT s.artist_key) AS versions,
                       json_group_array(DISTINCT s.artist) AS originals
                       FROM scrobbles s WHERE s.active=1
                       GROUP BY s.artist_group_key ORDER BY plays DESC"""
                )
            ]
        from .grouping import artist_suggestion_key
        for a in artists:
            a["originals"] = json.loads(a["originals"] or "[]")
        buckets = {}
        for a in artists:
            key = artist_suggestion_key(a["name"])
            if len(key) >= 3:
                buckets.setdefault(key, []).append(a)
        suggestions = []
        for members in buckets.values():
            if len(members) > 1:
                for i, left in enumerate(members):
                    for right in members[i+1:]:
                        suggestions.append(dict(
                            ids=[left["id"], right["id"]],
                            names=[left["name"], right["name"]],
                            plays=left["plays"]+right["plays"],
                            reason="Names differ by article, accents or punctuation"
                        ))
        suggestions.sort(key=lambda p: -p["plays"])
        if needle:
            artists = [a for a in artists if needle in normalise(
                a["name"] + " " + " ".join(a["originals"]))]
            suggestions = [p for p in suggestions if needle in normalise(" ".join(p["names"]))]
        return jsonify(rows=artists[offset:offset+50], total=len(artists), offset=offset,
                       suggestions=suggestions[:25])

    @app.get("/api/grouping-review")
    def grouping_review():
        return jsonify(
            review(
                db_for_request(),
                request.args.get("kind", "song"),
                request.args.get("tab", "suggested"),
                request.args.get("q", "")[:200],
                max(0, int(request.args.get("offset", 0))),
            )
        )

    @app.post("/api/grouping")
    def grouping():
        data = request.get_json()
        if not isinstance(data, dict) or not isinstance(data.get("ids", []), list):
            raise ValueError("Invalid grouping request")
        db = db_for_request()
        if data.get("action") in ("dismiss", "restore"):
            key = data.get("key", "")
            if not re.fullmatch(r"(?:song|album):[0-9]+:[0-9]+", key):
                raise ValueError("Invalid candidate")
            with db.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                old = db.get(conn, "dismissed_candidates", [])
                new = [k for k in old if k != key]
                if data["action"] == "dismiss":
                    new.append(key)
                db.put(conn, "dismissed_candidates", new)
                conn.execute(
                    "INSERT INTO grouping_events(ts,description,before_json) VALUES (?,?,?)",
                    (
                        int(time.time()),
                        "Updated candidate decision",
                        json.dumps(
                            {"aliases": [], "variants": [], "dismissed_candidates": old}
                        ),
                    ),
                )
        elif data.get("action") == "undo":
            db.undo_grouping()
        elif data.get("action") == "merge_artists":
            db.change_artists(data.get("ids", []), data.get("name"))
        else:
            db.change_groups(data.get("action"), data.get("ids", []), data.get("name"))
        view_cache.clear(db)
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
        view_cache.warm(database)
    return app
