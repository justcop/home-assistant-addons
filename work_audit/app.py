import csv
import datetime as dt
import io
import json
import logging
import os
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request
from engine import Audit


def create_app(audit):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 4096

    @app.before_request
    def ingress_only():
        if request.remote_addr != "172.30.32.2":
            abort(403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/")
    def index():
        return render_template("index.html", today=dt.datetime.now(audit.zone).date().isoformat(),
                               base_path=request.headers.get("X-Ingress-Path", "").rstrip("/") + "/")

    def report():
        day = request.args.get("date", dt.datetime.now(audit.zone).date().isoformat())
        return audit.day(day, request.args.get("work_only") == "1")

    @app.get("/api/day")
    def day():
        try:
            return jsonify(report())
        except ValueError:
            return jsonify(error="Use a date in YYYY-MM-DD format"), 400

    @app.get("/api/entities")
    def entities():
        with audit.lock:
            return jsonify(entities=audit.candidates, connected=audit.ha_connected)

    @app.post("/api/session")
    def session():
        # Same-origin fetch header prevents cross-site form submissions.
        if request.headers.get("X-Work-Audit") != "1":
            return jsonify(error="Missing request header"), 403
        try:
            audit.set_override((request.get_json(silent=True) or {}).get("mode"))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return jsonify(mode=audit.override)

    @app.get("/api/export.csv")
    def export():
        try:
            data = report()
        except ValueError:
            return jsonify(error="Invalid date"), 400
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Track", "Start", "End", "Seconds", "Observation", "App", "Context"])
        windows = [(s["start"], s["end"]) for s in data["tracks"]["work"] if s["value"] == "at_work"] if data["work_only"] else [(data["start"], data["end"])]
        def safe(value):
            text = str(value)
            return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
        for track, segments in data["tracks"].items():
            for s in segments:
                for a, b in windows:
                    start, end = max(a, s["start"]), min(b, s["end"])
                    if end <= start:
                        continue
                    writer.writerow([track, dt.datetime.fromtimestamp(start, audit.zone).isoformat(),
                                     dt.datetime.fromtimestamp(end, audit.zone).isoformat(), round(end-start, 1),
                                     safe(s["value"]), safe(s["detail"].get("app", "")), safe(s["detail"].get("context", ""))])
        return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": 'attachment; filename="work-audit-' + data["date"] + '.csv"'})

    return app


if __name__ == "__main__":
    from collectors import start
    from waitress import serve
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    options_path = Path(os.environ.get("WORK_AUDIT_OPTIONS", "/data/options.json"))
    options = json.loads(options_path.read_text()) if options_path.exists() else {}
    data_dir = Path(os.environ.get("WORK_AUDIT_DATA", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    audit = Audit(data_dir / "work-audit.sqlite3", options)
    start(audit, options)
    # No host port exposed. Home Assistant authenticates access through ingress.
    serve(create_app(audit), host="0.0.0.0", port=8099, threads=4)
