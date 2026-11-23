"""Small ingress-only administrator UI with no application credentials."""
import json
from pathlib import Path
import secrets
import threading
import time

from flask import Flask, abort, jsonify, render_template, request
from waitress import serve

from updater import Updater, supervisor


def create_app(updater, options, testing=False):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8192
    csrf = secrets.token_urlsafe(32)
    cache = {"until": 0, "names": set()}

    @app.before_request
    def authorize():
        if request.remote_addr != "172.30.32.2":
            abort(403)
        if not testing:
            if time.monotonic() >= cache["until"]:
                users = supervisor("/auth/list")["users"]
                cache["names"] = {u["username"] for u in users if u.get("is_active") and
                                  (u.get("is_owner") or "system-admin" in u.get("group_ids", []))}
                cache["until"] = time.monotonic() + 30
            if request.headers.get("X-Remote-User-Name") not in cache["names"]:
                abort(403)
        if request.method != "GET":
            if not secrets.compare_digest(csrf, request.headers.get("X-CSRF-Token", "")):
                abort(403)
            if not request.is_json:
                abort(415)

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'self'; object-src 'none'"})
        return response

    @app.get("/")
    def index():
        return render_template("index.html", csrf=csrf, base=request.headers.get("X-Ingress-Path", "").rstrip("/"))

    @app.get("/api/status")
    def status():
        return jsonify({**updater.state, "busy": updater.busy, "automatic_updates": options.get("automatic_updates", True)})

    @app.post("/api/action")
    def action():
        body = request.get_json()
        if not isinstance(body, dict) or body.get("action") not in {"check", "deploy", "recover", "resume"}:
            abort(400)
        ident, sha = body.get("id"), body.get("sha") or None
        if body["action"] != "check" and ident not in updater.state["apps"]:
            abort(400)
        if sha is not None:
            import re
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                abort(400)
        if (sha or body["action"] == "recover") and body.get("acknowledge_data") is not True:
            return jsonify(error="Code redeployment does not reverse database changes"), 400
        if updater.busy:
            return jsonify(error="Another operation is running"), 409
        threading.Thread(target=updater.run, args=(body["action"], ident, sha), daemon=True).start()
        return jsonify(accepted=True), 202

    return app


if __name__ == "__main__":
    options = json.loads(Path("/data/options.json").read_text())
    updater = Updater()

    def automatic():
        while True:
            updater.run("automatic" if options.get("automatic_updates", True) else "check")
            time.sleep(max(120, options.get("check_interval", 180)))

    threading.Thread(target=automatic, daemon=True).start()
    serve(create_app(updater, options), host="0.0.0.0", port=8099, threads=6)
