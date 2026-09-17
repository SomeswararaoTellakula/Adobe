#!/usr/bin/env python3
"""Brand AI readiness audit application with authentication and dashboard."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bson import ObjectId
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent
AUDIT_SCRIPT = ROOT / "skills" / "audit-orchestrator" / "scripts" / "run_audit.py"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "brand-audit-secret-key")
app.config["UPLOAD_FOLDER"] = str(ROOT / "audit-output")

FALLBACK_DB = {"users": [], "audits": []}
DEFAULT_DEMO_USER = {
    "email": "demo@brandaudit.ai",
    "password": "brandaudit123",
    "name": "Demo User",
}

ANALYSIS_DIMENSIONS = (
    ("access", "Access", "Can crawlers and AI agents reach the site?", "discoverability.access"),
    ("extractability", "Extractability", "Can machines read the content that visitors see?", "discoverability.extractability"),
    ("semantics", "Semantics", "Are the brand and its facts structured clearly?", "discoverability.semantics"),
    ("answerability", "Answerability", "Can assistants quote useful, self-contained answers?", "discoverability.answerability"),
    ("trust", "Freshness & corroboration", "Is the information current, attributable, and corroborated?", "discoverability.trust"),
    ("engagement", "Engagement", "Can referred visitors understand and continue?", "engagement.landing"),
)


def create_server(host="127.0.0.1", port=8000):
    return make_server(host, port, app)


def build_analysis_sections(report):
    findings = report.get("findings", [])
    return [
        {
            "key": key,
            "label": label,
            "description": description,
            "findings": [finding for finding in findings if finding.get("dimension") == dimension],
        }
        for key, label, description, dimension in ANALYSIS_DIMENSIONS
    ]


def ensure_demo_user():
    demo_email = DEFAULT_DEMO_USER["email"]
    db = database()
    if db is not None:
        existing = db.users.find_one({"email": demo_email})
        if not existing:
            db.users.insert_one({
                "_id": ObjectId(),
                "name": DEFAULT_DEMO_USER["name"],
                "email": demo_email,
                "password_hash": generate_password_hash(DEFAULT_DEMO_USER["password"]),
                "created_at": datetime.now(timezone.utc),
            })
        return

    for user in fallback_user_store():
        if user["email"] == demo_email:
            return
    fallback_user_store().append({
        "_id": "demo-user",
        "name": DEFAULT_DEMO_USER["name"],
        "email": demo_email,
        "password_hash": generate_password_hash(DEFAULT_DEMO_USER["password"]),
        "created_at": datetime.now(timezone.utc),
    })


def is_valid_http_url(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def mongo_client():
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        return client
    except Exception:
        return None


def database():
    client = mongo_client()
    if client is None:
        return None
    db = client[os.environ.get("MONGO_DB", "brand_audit")]
    db.users.create_index("email", unique=True)
    db.audits.create_index("user_id")
    return db


def fallback_user_store():
    return FALLBACK_DB["users"]


def fallback_audit_store():
    return FALLBACK_DB["audits"]


def current_user():
    ensure_demo_user()
    email = session.get("user_email")
    if not email:
        return None
    db = database()
    if db is not None:
        user = db.users.find_one({"email": email})
        return user
    for user in fallback_user_store():
        if user["email"] == email:
            return user
    return None


def require_login(view):
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login_page"))
        return view(*args, **kwargs)

    wrapped.__name__ = view.__name__
    return wrapped


def run_audit(url: str, out_dir: str, max_pages: int = 25, budget: float = 240.0):
    if not is_valid_http_url(url):
        raise ValueError("Enter a valid http:// or https:// URL.")
    cmd = [
        sys.executable,
        str(AUDIT_SCRIPT),
        "--url",
        url,
        "--out-dir",
        out_dir,
        "--max-pages",
        str(max_pages),
        "--budget",
        str(budget),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode not in (0, 2):
        raise RuntimeError(result.stderr.strip() or "Audit failed without a clear error message.")
    return result


def read_report(out_dir: str):
    report_json = Path(out_dir) / "report.json"
    summary_txt = Path(out_dir) / "summary.txt"
    summary_text = summary_txt.read_text(encoding="utf-8", errors="replace") if summary_txt.exists() else ""
    if report_json.exists():
        try:
            report = json.loads(report_json.read_text(encoding="utf-8"))
            report["summary_text"] = report.get("summary_text") or summary_text
            return report
        except json.JSONDecodeError:
            pass
    if summary_text:
        return {"summary_text": summary_text}
    return {"summary_text": "No report was generated yet."}


@app.route("/")
def index():
    return render_template("home.html")


@app.route("/landing")
def landing_page():
    return render_template("home.html")


@app.route("/app")
def app_home():
    if current_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login_page"))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    ensure_demo_user()
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not email or not password:
            flash("Email and password are required.", "danger")
            return redirect(url_for("login_page"))

        db = database()
        if db is not None:
            user = db.users.find_one({"email": email})
            if not user or not check_password_hash(user["password_hash"], password):
                flash("Invalid email or password.", "danger")
                return redirect(url_for("login_page"))
            session["user_email"] = user["email"]
            flash("Welcome back.", "success")
            return redirect(url_for("dashboard"))

        for user in fallback_user_store():
            if user["email"] == email and check_password_hash(user["password_hash"], password):
                session["user_email"] = user["email"]
                flash("Welcome back.", "success")
                return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "danger")
        return redirect(url_for("login_page"))
    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup_page():
    ensure_demo_user()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not name or not email or not password:
            flash("Name, email, and password are required.", "danger")
            return redirect(url_for("signup_page"))

        db = database()
        if db is not None:
            existing = db.users.find_one({"email": email})
            if existing:
                flash("This account already exists. Please sign in.", "warning")
                return redirect(url_for("login_page"))
            user = {
                "_id": ObjectId(),
                "name": name,
                "email": email,
                "password_hash": generate_password_hash(password),
                "created_at": datetime.now(timezone.utc),
            }
            db.users.insert_one(user)
            session["user_email"] = email
            flash("Account created successfully.", "success")
            return redirect(url_for("dashboard"))

        for existing in fallback_user_store():
            if existing["email"] == email:
                flash("This account already exists. Please sign in.", "warning")
                return redirect(url_for("login_page"))
        user = {
            "_id": f"local-{len(fallback_user_store()) + 1}",
            "name": name,
            "email": email,
            "password_hash": generate_password_hash(password),
            "created_at": datetime.now(timezone.utc),
        }
        fallback_user_store().append(user)
        session["user_email"] = email
        flash("Account created successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.pop("user_email", None)
    flash("You have signed out.", "info")
    return redirect(url_for("login_page"))


@app.route("/dashboard")
@require_login
def dashboard():
    user = current_user()
    db = database()
    if db is not None:
        audits = list(db.audits.find({"user_id": user["_id"]}).sort("created_at", -1))
        for item in audits:
            item["id"] = str(item["_id"])
            item.pop("_id", None)
    else:
        audits = [
            audit for audit in fallback_audit_store() if audit.get("user_id") == user["_id"]
        ]
        audits = sorted(audits, key=lambda x: x.get("created_at", ""), reverse=True)
    summary = {
        "total": len(audits),
        "critical": sum(1 for a in audits if (a.get("summary") or {}).get("critical", 0) > 0),
        "high": sum(1 for a in audits if (a.get("summary") or {}).get("high", 0) > 0),
        "sites": len({a.get("site") for a in audits if a.get("site")})
    }
    db_ready = db is not None
    return render_template("dashboard.html", user=user, audits=audits, summary=summary, db_ready=db_ready)


@app.route("/audit/new", methods=["POST"])
@require_login
def create_audit():
    url = (request.form.get("url") or "").strip()
    max_pages = int(request.form.get("max_pages", 25) or 25)
    budget = float(request.form.get("budget", 240) or 240)

    if not is_valid_http_url(url):
        flash("Please enter a valid http or https URL.", "danger")
        return redirect(url_for("dashboard"))

    user = current_user()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    out_dir = ROOT / "audit-output" / user["email"].replace("@", "_") / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        run_audit(url, str(out_dir), max_pages=max_pages, budget=budget)
        report = read_report(str(out_dir))
        report_summary = report.get("summary", {})
        record = {
            "user_id": user["_id"],
            "site": report.get("site") or url,
            "url": url,
            "created_at": datetime.now(timezone.utc),
            "status": "complete",
            "summary": {
                "total_findings": report_summary.get("total_findings", 0),
                "critical": report_summary.get("critical", 0),
                "high": report_summary.get("high", 0),
                "medium": report_summary.get("medium", 0),
                "low": report_summary.get("low", 0),
                "info": report_summary.get("info", 0),
            },
            "report_path": str(out_dir),
            "scope": report.get("scope", {}),
            "brief": report.get("summary_text") or "Audit completed.",
        }
        db = database()
        if db is not None:
            db.audits.insert_one(record)
        else:
            record["_id"] = f"local-audit-{len(fallback_audit_store()) + 1}"
            fallback_audit_store().append(record)
        flash("Audit completed and saved to your dashboard.", "success")
    except Exception as exc:
        flash(f"Audit failed: {exc}", "danger")
    return redirect(url_for("dashboard"))


@app.route("/audit/<audit_id>")
@require_login
def audit_detail(audit_id: str):
    user = current_user()
    db = database()
    if db is not None:
        audit = db.audits.find_one({"_id": ObjectId(audit_id), "user_id": user["_id"]})
        if not audit:
            flash("Audit not found.", "warning")
            return redirect(url_for("dashboard"))
        audit["id"] = str(audit["_id"])
        audit.pop("_id", None)
    else:
        audit = next((a for a in fallback_audit_store() if str(a.get("_id") or a.get("id")) == audit_id and a.get("user_id") == user["_id"]), None)
        if not audit:
            flash("Audit not found.", "warning")
            return redirect(url_for("dashboard"))
    report_path = Path(audit.get("report_path", ROOT / "audit-output"))
    report = read_report(str(report_path)) if report_path.exists() else {"summary_text": "No readable report was found."}
    analysis_sections = build_analysis_sections(report)
    return render_template("audit_detail.html", user=user, audit=audit, report=report, analysis_sections=analysis_sections)


@app.route("/health")
def health():
    db = database()
    return jsonify({
        "status": "ok",
        "mongo_connected": db is not None,
        "user_count": len(fallback_user_store()) if db is None else db.users.count_documents({}),
    })


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    app.run(host=host, port=port, debug=True)
