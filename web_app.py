import hashlib
import logging
import os
import subprocess
import tempfile
import time
import sys
import importlib.machinery
import importlib.util
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from flask import Flask, request, render_template_string, send_from_directory, url_for, Response, redirect, session
from flask_session import Session
from tools.job_discovery import rank_job_postings
from tools.resume_bot import (
    assess_job_fit,
    choose_resume_strategy,
    classify_job,
    generate_cover_letter,
    generate_resume,
    load_resume_json,
    resume_json_to_text,
)

APP_ROOT = Path(__file__).resolve().parent


def load_supabase_create_client():
    search_paths = []
    for raw_path in sys.path:
        candidate = Path(raw_path or '.').resolve()
        if candidate == APP_ROOT:
            continue
        search_paths.append(raw_path)
    spec = importlib.machinery.PathFinder.find_spec('supabase', search_paths)
    if not spec or not spec.loader:
        raise ImportError('supabase package is not installed.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, 'create_client')


def load_env_file(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip().lstrip('\ufeff')
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(APP_ROOT / '.env')

DEFAULT_RESUME = os.environ.get('RESUME_JSON', str(APP_ROOT / 'assets' / 'resume.json'))
DEFAULT_TEMPLATE = os.environ.get('RESUME_TEMPLATE', str(APP_ROOT / 'assets' / 'template.html'))
OUTPUT_DIR = os.environ.get('RESUME_OUTPUT_DIR', str(Path(tempfile.gettempdir()) / 'job-application-assistant'))
MAX_MONTHLY_GENERATIONS = 10
SUPABASE_URL = (os.environ.get('SUPABASE_URL') or '').strip()
SUPABASE_ANON_KEY = (os.environ.get('SUPABASE_ANON_KEY') or '').strip()
SUPABASE_SERVICE_KEY = (os.environ.get('SUPABASE_SERVICE_KEY') or '').strip()
UNLIMITED_USAGE_EMAILS = {
    x.strip().lower()
    for x in (os.environ.get('UNLIMITED_USAGE_EMAILS') or '').split(',')
    if x.strip()
}

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'change-me-in-production')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = str(APP_ROOT / '.flask_session')
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
Session(app)

try:
    create_client = load_supabase_create_client()
except Exception:
    create_client = None

SUPABASE_AUTH_CLIENT = None
SUPABASE_SERVICE_CLIENT = None
if create_client and SUPABASE_URL and SUPABASE_ANON_KEY:
    SUPABASE_AUTH_CLIENT = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
if create_client and SUPABASE_URL and SUPABASE_SERVICE_KEY:
    SUPABASE_SERVICE_CLIENT = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def supabase_is_configured() -> bool:
    return bool(SUPABASE_AUTH_CLIENT and SUPABASE_SERVICE_CLIENT)


def current_user() -> dict | None:
    user_id = session.get('user_id')
    email = session.get('user_email')
    access_token = session.get('sb_access_token')
    refresh_token = session.get('sb_refresh_token')
    if not (user_id and email and access_token and refresh_token):
        return None
    return {
        'id': user_id,
        'email': email,
        'access_token': access_token,
        'refresh_token': refresh_token,
    }


def has_unlimited_usage(user: dict | None) -> bool:
    if not user:
        return False
    return (user.get('email') or '').strip().lower() in UNLIMITED_USAGE_EMAILS


def login_required(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for('login', next=request.path))
        return handler(*args, **kwargs)
    return wrapped


def _safe_next_path(next_value: str | None) -> str:
    if next_value and next_value.startswith('/') and not next_value.startswith('//'):
        return next_value
    return url_for('index')


def _extract_job_title(job_text: str) -> str:
    for line in job_text.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate[:120]
    return 'Unknown role'


def _get_local_generations() -> list[dict]:
    return list(session.get('local_generations') or [])


def _get_local_job_leads() -> list[dict]:
    return list(session.get('local_job_leads') or [])


def _job_lead_hash(job: dict) -> str:
    seed = '|'.join([
        str(job.get('url') or '').strip().lower(),
        str(job.get('title') or '').strip().lower(),
        str(job.get('company') or '').strip().lower(),
        str(job.get('description') or '').strip().lower(),
    ])
    return hashlib.sha256(seed.encode('utf-8', errors='replace')).hexdigest()


def _append_local_generation(job_title: str, detected_role_type: str, status: str) -> None:
    rows = _get_local_generations()
    rows.append(
        {
            'created_at': datetime.now(timezone.utc).isoformat(),
            'job_title': job_title,
            'detected_role_type': detected_role_type,
            'status': status,
        }
    )
    session['local_generations'] = rows[-200:]


def _month_window_utc(now_utc: datetime | None = None) -> tuple[str, str]:
    now_utc = now_utc or datetime.now(timezone.utc)
    start = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start.isoformat(), end.isoformat()


def _get_user_data_client():
    user = current_user()
    if not user or not SUPABASE_AUTH_CLIENT:
        return None
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    client.auth.set_session(user['access_token'], user['refresh_token'])
    return client


def get_current_month_generation_count() -> int:
    user = current_user()
    client = SUPABASE_SERVICE_CLIENT
    if not client or not user:
        return 0
    try:
        month_start, month_end = _month_window_utc()
        resp = (
            client.table('generations')
            .select('id', count='exact')
            .eq('user_id', user['id'])
            .gte('created_at', month_start)
            .lt('created_at', month_end)
            .execute()
        )
        return int(resp.count or 0)
    except Exception:
        logging.exception('Failed to query monthly generation count')
        month_start, month_end = _month_window_utc()
        count = 0
        for row in _get_local_generations():
            created = str(row.get('created_at') or '')
            if month_start <= created < month_end:
                count += 1
        return count


def get_generation_history(limit: int = 50) -> list[dict]:
    user = current_user()
    client = SUPABASE_SERVICE_CLIENT
    if not client or not user:
        return []
    try:
        resp = (
            client.table('generations')
            .select('created_at,job_title,detected_role_type,status')
            .eq('user_id', user['id'])
            .order('created_at', desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception:
        logging.exception('Failed to query generation history')
        rows = _get_local_generations()
        rows.sort(key=lambda x: str(x.get('created_at') or ''), reverse=True)
        return rows[:limit]


def record_generation(job_title: str, detected_role_type: str, status: str) -> None:
    user = current_user()
    client = SUPABASE_SERVICE_CLIENT
    if not user or not client:
        return
    try:
        (
            client.table('generations')
            .insert(
                {
                    'user_id': user['id'],
                    'job_title': job_title,
                    'detected_role_type': detected_role_type,
                    'status': status,
                }
            )
            .execute()
        )
    except Exception:
        logging.exception('Failed to insert generation record')
        _append_local_generation(
            job_title=job_title,
            detected_role_type=detected_role_type,
            status=status,
        )


def save_job_leads(ranked_jobs: list[dict]) -> int:
    user = current_user()
    if not ranked_jobs:
        return 0
    rows = []
    for job in ranked_jobs:
        rows.append({
            'user_id': (user or {}).get('id'),
            'job_hash': _job_lead_hash(job),
            'title': job.get('title') or 'Untitled role',
            'company': job.get('company') or '',
            'location': job.get('location') or '',
            'url': job.get('url') or '',
            'platform': job.get('platform') or 'Unknown',
            'description': job.get('description') or '',
            'score': int(job.get('score') or 0),
            'recommendation': job.get('recommendation') or 'REVIEW',
            'detected_role_type': job.get('detected_role_type') or 'unknown',
            'matched_terms': job.get('matched_terms') or [],
            'missing_terms': job.get('missing_terms') or [],
            'positive_signals': job.get('positive_signals') or [],
            'risk_signals': job.get('risk_signals') or [],
            'preference_signals': job.get('preference_signals') or [],
            'reasons': job.get('reasons') or [],
            'status': 'shortlisted',
        })

    client = SUPABASE_SERVICE_CLIENT
    if user and client:
        try:
            (
                client.table('job_leads')
                .upsert(rows, on_conflict='user_id,job_hash')
                .execute()
            )
            return len(rows)
        except Exception:
            logging.exception('Failed to upsert job leads; falling back to session storage')

    existing = {row.get('job_hash'): row for row in _get_local_job_leads()}
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        row['user_id'] = row.get('user_id') or 'local'
        row.setdefault('created_at', now)
        row['updated_at'] = now
        row['id'] = row['job_hash']
        existing[row['job_hash']] = row
    local_rows = sorted(existing.values(), key=lambda x: int(x.get('score') or 0), reverse=True)
    session['local_job_leads'] = local_rows[:200]
    return len(rows)


def get_job_leads(limit: int = 100) -> list[dict]:
    user = current_user()
    client = SUPABASE_SERVICE_CLIENT
    if user and client:
        try:
            resp = (
                client.table('job_leads')
                .select('id,created_at,updated_at,title,company,location,url,platform,description,score,recommendation,detected_role_type,status,matched_terms,risk_signals,preference_signals,reasons,generated_resume_html,generated_resume_pdf,generated_cover_letter,generated_cover_pdf')
                .eq('user_id', user['id'])
                .order('score', desc=True)
                .order('created_at', desc=True)
                .limit(limit)
                .execute()
            )
            return resp.data or []
        except Exception:
            logging.exception('Failed to query job leads; falling back to session storage')
    rows = _get_local_job_leads()
    rows.sort(key=lambda x: (int(x.get('score') or 0), str(x.get('created_at') or '')), reverse=True)
    return rows[:limit]


def get_job_lead(lead_id: str) -> dict | None:
    user = current_user()
    client = SUPABASE_SERVICE_CLIENT
    if user and client:
        try:
            resp = (
                client.table('job_leads')
                .select('id,created_at,updated_at,title,company,location,url,platform,description,score,recommendation,detected_role_type,status,matched_terms,missing_terms,positive_signals,risk_signals,preference_signals,reasons,generated_resume_html,generated_resume_pdf,generated_cover_letter,generated_cover_pdf')
                .eq('user_id', user['id'])
                .eq('id', lead_id)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            return rows[0] if rows else None
        except Exception:
            logging.exception('Failed to query job lead; falling back to session storage')
    for row in _get_local_job_leads():
        if str(row.get('id') or row.get('job_hash')) == str(lead_id):
            return row
    return None


def record_job_lead_outputs(lead_id: str, *, resume_html: str = '', resume_pdf: str = '', cover_letter: str = '', cover_pdf: str = '') -> None:
    if not lead_id:
        return
    user = current_user()
    client = SUPABASE_SERVICE_CLIENT
    patch = {
        'status': 'generated',
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'generated_resume_html': resume_html,
        'generated_resume_pdf': resume_pdf,
        'generated_cover_letter': cover_letter,
        'generated_cover_pdf': cover_pdf,
    }
    if user and client:
        try:
            (
                client.table('job_leads')
                .update(patch)
                .eq('user_id', user['id'])
                .eq('id', lead_id)
                .execute()
            )
            return
        except Exception:
            logging.exception('Failed to update job lead outputs; falling back to session storage')
    rows = _get_local_job_leads()
    for row in rows:
        if str(row.get('id') or row.get('job_hash')) == str(lead_id):
            row.update(patch)
            break
    session['local_job_leads'] = rows


def format_created_at(value: str | None) -> str:
    raw = (value or '').strip()
    if not raw:
        return '-'
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        hour_12 = dt.hour % 12 or 12
        return f"{dt.day:02d} {dt.strftime('%b %Y')}, {hour_12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"
    except Exception:
        return raw


def get_app_version() -> str:
    env_version = os.environ.get('APP_VERSION')
    if env_version:
        return env_version.strip()
    try:
        out = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=str(APP_ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        version = out.strip()
        if version:
            return version
    except Exception:
        pass
    return 'unknown'


def cleanup_old_output_files(max_age_seconds: int = 24 * 60 * 60) -> None:
    output_path = Path(OUTPUT_DIR)
    if not output_path.exists():
        return
    cutoff = time.time() - max_age_seconds
    for path in output_path.iterdir():
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except Exception:
            logging.exception("Failed to delete old output file: %s", path)


PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Resume Tailor</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Space+Grotesk:wght@500;700&display=swap');
    :root {
      --ink: #e2e8f0;
      --muted: #94a3b8;
      --accent: #67e8f9;
      --accent-2: #22c55e;
      --panel: #0f172a;
      --panel-2: #020617;
      --stroke: #334155;
      --shadow: 0 18px 60px -24px rgba(6, 182, 212, 0.45);
      --bg-base: #030712;
      --bg-radial-1: rgba(34, 197, 94, 0.16);
      --bg-radial-2: rgba(6, 182, 212, 0.2);
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "DM Sans", "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1200px 600px at 15% 10%, var(--bg-radial-1) 0%, rgba(34,197,94,0) 38%),
        radial-gradient(1000px 600px at 80% 12%, var(--bg-radial-2) 0%, rgba(6,182,212,0) 36%),
        radial-gradient(900px 500px at 50% 85%, rgba(14,116,144,0.2) 0%, rgba(14,116,144,0) 42%),
        var(--bg-base);
      background-repeat: no-repeat;
      background-attachment: fixed;
      min-height: 100vh;
    }

    .shell {
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px 20px 60px;
    }

    .header {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 18px;
    }

    h1 { font-family: "Space Grotesk", sans-serif; font-size: 26px; margin: 0 0 6px; letter-spacing: -0.5px; }
    .hint { color: var(--muted); font-size: 13.5px; }

    .card {
      background: var(--panel);
      border: 1px solid var(--stroke);
      border-radius: 16px;
      box-shadow: var(--shadow);
      padding: 18px;
    }

    label { display: block; font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--muted); margin: 12px 0 6px; }
    textarea {
      width: 100%;
      min-height: 260px;
      font-family: ui-monospace, "JetBrains Mono", Consolas, monospace;
      font-size: 13px;
      padding: 12px 14px;
      border: 1px solid var(--stroke);
      border-radius: 12px;
      background: var(--panel);
      color: var(--ink);
    }

    .form-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
    button {
      padding: 10px 14px;
      font-size: 14px;
      border-radius: 10px;
      border: 1px solid transparent;
      background: linear-gradient(135deg, #0e7490, #06b6d4);
      color: #fff;
      cursor: pointer;
    }
    button[disabled] { opacity: 0.6; cursor: not-allowed; }
    .secondary-btn { background: var(--panel-2); color: var(--ink); border: 1px solid var(--stroke); }
    .header-right {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .nav-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 8px 12px;
      border-radius: 10px;
      border: 1px solid var(--stroke);
      background: var(--panel-2);
      color: var(--ink);
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
    }
    .nav-link:hover {
      border-color: #0e7490;
    }

    .loading { display: none; align-items: center; gap: 10px; margin-top: 12px; font-size: 13px; color: var(--muted); }
    .spinner {
      width: 16px; height: 16px; border: 2px solid #334155; border-top-color: var(--accent);
      border-radius: 50%; animation: spin 0.9s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .result { margin-top: 16px; padding: 14px; background: var(--panel-2); border-radius: 14px; border: 1px solid var(--stroke); }
    .actions { display: flex; gap: 10px; margin: 8px 0 12px; flex-wrap: wrap; }
    .actions a {
      display: inline-block;
      padding: 8px 12px;
      background: linear-gradient(135deg, #0e7490, #06b6d4);
      color: #ecfeff;
      text-decoration: none;
      border-radius: 10px;
      font-size: 13px;
      border: 1px solid #0e7490;
    }
    .preview { width: 100%; height: 780px; border: 1px solid var(--stroke); border-radius: 12px; background: var(--panel); }
    .cover-box {
      width: 100%;
      min-height: 260px;
      border: 1px solid var(--stroke);
      border-radius: 12px;
      padding: 12px;
      background: var(--panel);
      color: var(--ink);
      font-family: ui-monospace, "JetBrains Mono", Consolas, monospace;
      font-size: 12.5px;
      white-space: pre-wrap;
    }

    .grid {
      display: grid;
      grid-template-columns: 1.1fr 1fr;
      gap: 18px;
    }
    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr; }
      .preview { height: 640px; }
    }
    .alert {
      margin-top: 10px;
      padding: 10px 12px;
      border-radius: 10px;
      font-size: 13px;
      border: 1px solid #7f1d1d;
      background: #1f0a0a;
      color: #fecaca;
    }
    .fit-card {
      margin: 0 0 12px;
      padding: 12px;
      border-radius: 12px;
      border: 1px solid var(--stroke);
      background: var(--panel);
    }
    .fit-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 6px;
    }
    .fit-pill {
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid transparent;
    }
    .fit-apply { background: #052e16; color: #86efac; border-color: #166534; }
    .fit-maybe { background: #422006; color: #fde68a; border-color: #854d0e; }
    .fit-no { background: #450a0a; color: #fecaca; border-color: #991b1b; }
    .fit-meta { font-size: 12px; color: var(--muted); }
    .fit-rationale { font-size: 13px; margin: 6px 0; }
    .fit-gaps { margin: 0; padding-left: 18px; font-size: 12px; color: var(--muted); }
    .fit-breakdown-title {
      margin-top: 10px;
      margin-bottom: 6px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--muted);
    }
    .fit-list {
      margin: 0;
      padding-left: 18px;
      font-size: 12px;
      color: var(--muted);
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="header">
      <div>
        <h1>Resume Tailor</h1>
        <div class="hint">Paste a job description and generate a tailored HTML + PDF. Build: <code>{{ app_version }}</code></div>
        <div class="hint">Signed in as {{ user_email }}. This month: {{ monthly_used }}/{{ monthly_limit }} generations.</div>
      </div>
      <div class="header-right">
        <a class="nav-link" href="{{ url_for('jobs') }}">Job Shortlist</a>
        <a class="nav-link" href="{{ url_for('dashboard') }}">Dashboard</a>
        <a class="nav-link" href="{{ url_for('logout') }}">Logout</a>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <form method="post" id="resume-form">
          <label>Job description</label>
          <textarea name="job_text" placeholder="Paste the full job description here...">{{ job_text_value }}</textarea>
          <div class="form-actions">
            <button type="submit" name="action" value="generate" id="generate-btn">Generate Resume</button>
            <button type="button" id="clear-btn" class="secondary-btn">Clear</button>
          </div>
          <div class="loading" id="loading">
            <div class="spinner"></div>
            <div id="loading-text">Parsing job description...</div>
          </div>
        </form>
        {% if error_msg %}
        <div class="alert">{{ error_msg }}</div>
        {% endif %}
      </div>

      <div class="card">
        {% if html_path or fit %}
        <div class="result" id="result">
          {% if fit %}
          <div class="fit-card">
            <div class="fit-row">
              <div class="fit-pill {% if fit.recommendation == 'APPLY' %}fit-apply{% elif fit.recommendation == 'NO' %}fit-no{% else %}fit-maybe{% endif %}">
                {% if fit.recommendation == 'APPLY' %}
                Recommended to apply
                {% elif fit.recommendation == 'NO' %}
                Not recommended yet
                {% else %}
                Borderline fit
                {% endif %}
              </div>
              <div class="fit-meta">Confidence: {{ fit.confidence }}%</div>
            </div>
            {% if fit.rationale %}
            <div class="fit-rationale">{{ fit.rationale }}</div>
            {% endif %}
            {% if detected_focus and strategy_name %}
            <div class="fit-meta">
              Detected focus: {{ detected_focus.primary_category|replace('_', ' ')|title }} (confidence {{ detected_focus.confidence }}%). Strategy: {{ strategy_name }}.
            </div>
            {% endif %}
            {% if fit.gaps %}
            <ul class="fit-gaps">
              {% for gap in fit.gaps %}
              <li>{{ gap }}</li>
              {% endfor %}
            </ul>
            {% endif %}
            {% if fit.matched_requirements %}
            <div class="fit-breakdown-title">Matched requirements</div>
            <ul class="fit-list">
              {% for req in fit.matched_requirements %}
              <li>{{ req }}</li>
              {% endfor %}
            </ul>
            {% endif %}
            {% if fit.missing_requirements %}
            <div class="fit-breakdown-title">Gap breakdown (missing)</div>
            <ul class="fit-list">
              {% for req in fit.missing_requirements %}
              <li>{{ req }}</li>
              {% endfor %}
            </ul>
            {% endif %}
          </div>
          {% endif %}
          {% if html_path %}
          <div class="actions">
            <a href="{{ html_url }}" target="_blank" rel="noopener">Download HTML</a>
            {# PDF download removed by request #}
            {% if cover_url %}
            <a href="{{ cover_url }}" target="_blank" rel="noopener">Download Cover Letter HTML</a>
            {% endif %}
          </div>
          <iframe class="preview" id="preview" src="{{ preview_url }}"></iframe>
          {% if cover_preview_url %}
          <div style="height:12px"></div>
          <label>Cover letter</label>
          <iframe class="preview" id="cover-preview" src="{{ cover_preview_url }}"></iframe>
          {% elif cover_text %}
          <div style="height:12px"></div>
          <label>Cover letter</label>
          <div class="cover-box">{{ cover_text }}</div>
          {% endif %}
          {% endif %}
        </div>
        {% else %}
        <div class="hint">Your tailored resume preview will appear here.</div>
        {% endif %}
      </div>
    </div>
  </div>
</body>
<script>
  const form = document.getElementById('resume-form');
  const submitButtons = form.querySelectorAll('button[type="submit"]');
  const clearBtn = document.getElementById('clear-btn');
  const loading = document.getElementById('loading');
  const loadingText = document.getElementById('loading-text');
  const steps = [
    'Parsing job description...',
    'Assessing fit...',
    'Tailoring resume...',
    'Rendering resume and cover letter...'
  ];
  let stepIdx = 0;
  let timer = null;

  let submitting = false;
  form.addEventListener('submit', (e) => {
    if (submitting) {
      e.preventDefault();
      return;
    }
    submitting = true;
    const result = document.getElementById('result');
    if (result) {
      result.remove();
    }
    // Keep the clicked submit button enabled so its name/value is posted.
    submitButtons.forEach((b) => { b.disabled = (b !== e.submitter); });
    loading.style.display = 'flex';
    loadingText.textContent = steps[0];
    timer = setInterval(() => {
      stepIdx = (stepIdx + 1) % steps.length;
      loadingText.textContent = steps[stepIdx];
    }, 1800);
  });

  clearBtn.addEventListener('click', () => {
    form.reset();
    const jobText = form.querySelector('textarea[name="job_text"]');
    if (jobText) {
      jobText.value = '';
    }
    submitButtons.forEach((b) => { b.disabled = false; });
    loading.style.display = 'none';
    submitting = false;
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    const result = document.getElementById('result');
    if (result) {
      result.remove();
    }
  });
</script>
</html>
"""


AUTH_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ title }}</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Space+Grotesk:wght@500;700&display=swap');
    :root {
      --ink: #e2e8f0;
      --muted: #94a3b8;
      --accent: #67e8f9;
      --panel: #0f172a;
      --panel-2: #020617;
      --stroke: #334155;
      --shadow: 0 18px 60px -24px rgba(6, 182, 212, 0.45);
      --bg-base: #030712;
      --bg-radial-1: rgba(34, 197, 94, 0.16);
      --bg-radial-2: rgba(6, 182, 212, 0.2);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "DM Sans", "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1200px 600px at 15% 10%, var(--bg-radial-1) 0%, rgba(34,197,94,0) 38%),
        radial-gradient(1000px 600px at 80% 12%, var(--bg-radial-2) 0%, rgba(6,182,212,0) 36%),
        radial-gradient(900px 500px at 50% 85%, rgba(14,116,144,0.2) 0%, rgba(14,116,144,0) 42%),
        var(--bg-base);
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 20px;
    }
    .card {
      width: 100%;
      max-width: 460px;
      background: var(--panel);
      border: 1px solid var(--stroke);
      border-radius: 16px;
      box-shadow: var(--shadow);
      padding: 20px;
    }
    h1 { margin: 0 0 6px; letter-spacing: -0.5px; font-family: "Space Grotesk", sans-serif; }
    .hint { color: var(--muted); font-size: 13px; margin-bottom: 16px; }
    label { display: block; font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--muted); margin: 12px 0 6px; }
    input {
      width: 100%;
      padding: 11px 12px;
      border: 1px solid var(--stroke);
      border-radius: 10px;
      background: var(--panel);
      color: var(--ink);
      font-size: 14px;
    }
    button {
      margin-top: 14px;
      width: 100%;
      padding: 11px 12px;
      border: 1px solid transparent;
      border-radius: 10px;
      background: linear-gradient(135deg, #0e7490, #06b6d4);
      color: white;
      font-size: 14px;
      cursor: pointer;
    }
    .sub {
      margin-top: 14px;
      color: var(--muted);
      font-size: 13px;
    }
    .sub a {
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
    }
    .alert {
      margin-bottom: 12px;
      padding: 10px 12px;
      border-radius: 10px;
      font-size: 13px;
      border: 1px solid #fecaca;
      background: #fef2f2;
      color: #991b1b;
    }
    .ok {
      border-color: #166534;
      background: #052e16;
      color: #86efac;
    }
  </style>
</head>
<body>
  <div class="card">
    <h1>{{ title }}</h1>
    <div class="hint">Resume Tailor account access via Supabase auth.</div>
    {% if error_msg %}
    <div class="alert">{{ error_msg }}</div>
    {% endif %}
    {% if success_msg %}
    <div class="alert ok">{{ success_msg }}</div>
    {% endif %}
    <form method="post">
      <input type="hidden" name="next" value="{{ next_path }}" />
      <label>Email</label>
      <input type="email" name="email" required autocomplete="email" />
      <label>Password</label>
      <input type="password" name="password" required autocomplete="current-password" />
      <button type="submit">{{ submit_label }}</button>
    </form>
    <div class="sub">
      {{ alt_text }} <a href="{{ alt_href }}">{{ alt_link }}</a>
    </div>
  </div>
</body>
</html>
"""


DASHBOARD_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Dashboard</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Space+Grotesk:wght@500;700&display=swap');
    :root {
      --ink: #e2e8f0;
      --muted: #94a3b8;
      --accent: #67e8f9;
      --panel: #0f172a;
      --panel-2: #020617;
      --stroke: #334155;
      --shadow: 0 18px 60px -24px rgba(6, 182, 212, 0.45);
      --bg-base: #030712;
      --bg-radial-1: rgba(34, 197, 94, 0.16);
      --bg-radial-2: rgba(6, 182, 212, 0.2);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "DM Sans", "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1200px 600px at 15% 10%, var(--bg-radial-1) 0%, rgba(34,197,94,0) 38%),
        radial-gradient(1000px 600px at 80% 12%, var(--bg-radial-2) 0%, rgba(6,182,212,0) 36%),
        radial-gradient(900px 500px at 50% 85%, rgba(14,116,144,0.2) 0%, rgba(14,116,144,0) 42%),
        var(--bg-base);
      min-height: 100vh;
    }
    .shell {
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px 20px 60px;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
      flex-wrap: wrap;
    }
    h1 { margin: 0; font-family: "Space Grotesk", sans-serif; }
    .hint { color: var(--muted); font-size: 13px; }
    .nav-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 8px 12px;
      border-radius: 10px;
      border: 1px solid var(--stroke);
      background: var(--panel-2);
      color: var(--ink);
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
    }
    .nav-link:hover {
      border-color: #0e7490;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--stroke);
      border-radius: 16px;
      box-shadow: var(--shadow);
      padding: 18px;
      margin-bottom: 14px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      text-align: left;
      padding: 9px 8px;
      border-bottom: 1px solid var(--stroke);
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 1px;
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="header">
      <div>
        <h1>Dashboard</h1>
        <div class="hint">{{ user_email }}</div>
        <div class="hint">Build: <code>{{ app_version }}</code></div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a class="nav-link" href="{{ url_for('jobs') }}">Job Shortlist</a>
        <a class="nav-link" href="{{ url_for('index') }}">Resume Tool</a>
        <a class="nav-link" href="{{ url_for('logout') }}">Logout</a>
      </div>
    </div>
    <div class="card">
      <strong>Generations this month:</strong> {{ monthly_used }}/{{ monthly_limit }}
    </div>
    <div class="card">
      <h3 style="margin-top:0">Past Generations</h3>
      {% if generations %}
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Job title</th>
            <th>Detected role type</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {% for item in generations %}
          <tr>
            <td>{{ item.created_at }}</td>
            <td>{{ item.job_title or '-' }}</td>
            <td>{{ item.detected_role_type or '-' }}</td>
            <td>{{ item.status or '-' }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <div class="hint">No generations yet.</div>
      {% endif %}
    </div>
  </div>
</body>
</html>
"""


JOBS_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Job Shortlist</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Space+Grotesk:wght@500;700&display=swap');
    :root {
      --ink: #e2e8f0;
      --muted: #94a3b8;
      --accent: #67e8f9;
      --panel: #0f172a;
      --panel-2: #020617;
      --stroke: #334155;
      --shadow: 0 18px 60px -24px rgba(6, 182, 212, 0.45);
      --bg-base: #030712;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "DM Sans", "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1200px 600px at 15% 10%, rgba(34,197,94,0.16) 0%, rgba(34,197,94,0) 38%),
        radial-gradient(1000px 600px at 80% 12%, rgba(6,182,212,0.2) 0%, rgba(6,182,212,0) 36%),
        var(--bg-base);
      min-height: 100vh;
    }
    .shell { max-width: 1180px; margin: 0 auto; padding: 28px 20px 60px; }
    .header { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 18px; flex-wrap: wrap; }
    h1, h2, h3 { font-family: "Space Grotesk", sans-serif; }
    h1 { margin: 0 0 6px; }
    .hint { color: var(--muted); font-size: 13px; }
    .nav-link, button, .action-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 8px 12px;
      border-radius: 10px;
      border: 1px solid var(--stroke);
      background: var(--panel-2);
      color: var(--ink);
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }
    button, .primary { background: linear-gradient(135deg, #0e7490, #06b6d4); border-color: #0e7490; color: #ecfeff; }
    .card { background: var(--panel); border: 1px solid var(--stroke); border-radius: 16px; box-shadow: var(--shadow); padding: 18px; margin-bottom: 14px; }
    label { display: block; font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--muted); margin: 12px 0 6px; }
    textarea { width: 100%; min-height: 260px; font-family: ui-monospace, "JetBrains Mono", Consolas, monospace; font-size: 13px; padding: 12px 14px; border: 1px solid var(--stroke); border-radius: 12px; background: var(--panel-2); color: var(--ink); }
    input, select { width: 100%; padding: 10px 12px; border: 1px solid var(--stroke); border-radius: 10px; background: var(--panel-2); color: var(--ink); }
    .prefs-grid { display: grid; grid-template-columns: 1fr 180px 160px 160px; gap: 10px; align-items: end; }
    @media (max-width: 860px) { .prefs-grid { grid-template-columns: 1fr; } }
    .form-actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
    .job-card { display: grid; grid-template-columns: 120px 1fr; gap: 14px; border-top: 1px solid var(--stroke); padding-top: 16px; margin-top: 16px; }
    .score { width: 82px; height: 82px; border-radius: 999px; display: grid; place-items: center; background: var(--panel-2); border: 1px solid var(--stroke); font-size: 24px; font-weight: 700; }
    .pill { display: inline-flex; padding: 4px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; margin-right: 6px; border: 1px solid var(--stroke); }
    .apply { background: #052e16; color: #86efac; border-color: #166534; }
    .review { background: #422006; color: #fde68a; border-color: #854d0e; }
    .skip { background: #450a0a; color: #fecaca; border-color: #991b1b; }
    .meta { color: var(--muted); font-size: 13px; margin: 4px 0 8px; }
    .terms { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
    .term { font-size: 12px; padding: 3px 7px; border-radius: 999px; background: var(--panel-2); color: var(--muted); border: 1px solid var(--stroke); }
    details { margin-top: 10px; color: var(--muted); }
    pre { white-space: pre-wrap; max-height: 280px; overflow: auto; background: var(--panel-2); border: 1px solid var(--stroke); border-radius: 10px; padding: 10px; color: var(--ink); }
    @media (max-width: 760px) { .job-card { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class="shell">
    <div class="header">
      <div>
        <h1>Job Shortlist</h1>
        <div class="hint">Paste job postings from LinkedIn, SEEK, Indeed, or anywhere else. Separate postings with <code>---</code> or <code>===</code>.</div>
        <div class="hint">This is approval-first: rank jobs, save a shortlist, and generate docs before any external application step.</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a class="nav-link" href="{{ url_for('index') }}">Resume Tool</a>
        <a class="nav-link" href="{{ url_for('dashboard') }}">Dashboard</a>
        <a class="nav-link" href="{{ url_for('logout') }}">Logout</a>
      </div>
    </div>

    <div class="card">
      <form method="post" enctype="multipart/form-data">
        <label>Job postings</label>
        <textarea name="job_posts" placeholder="Title: Graduate Software Engineer&#10;Company: Example Co&#10;Location: Sydney&#10;URL: https://...&#10;&#10;Paste the full job ad here...&#10;---&#10;Title: Cybersecurity Analyst...&#10;&#10;Or upload a CSV with title, company, location, url, platform, description columns.">{{ job_posts_value }}</textarea>
        <label>Optional CSV import</label>
        <input type="file" name="job_csv" accept=".csv,text/csv" />
        <div class="prefs-grid">
          <div>
            <label>Preferred locations</label>
            <input name="preferred_locations" value="{{ preferred_locations }}" placeholder="Sydney, Remote" />
          </div>
          <div>
            <label>Role focus</label>
            <select name="role_focus">
              <option value="both" {% if role_focus == 'both' %}selected{% endif %}>Software + Cyber</option>
              <option value="software" {% if role_focus == 'software' %}selected{% endif %}>Software</option>
              <option value="cyber" {% if role_focus == 'cyber' %}selected{% endif %}>Cybersecurity</option>
            </select>
          </div>
          <div>
            <label>Graduate/junior</label>
            <select name="prefer_junior">
              <option value="1" {% if prefer_junior %}selected{% endif %}>Prefer</option>
              <option value="0" {% if not prefer_junior %}selected{% endif %}>No preference</option>
            </select>
          </div>
          <div>
            <label>Senior roles</label>
            <select name="avoid_senior">
              <option value="1" {% if avoid_senior %}selected{% endif %}>Avoid</option>
              <option value="0" {% if not avoid_senior %}selected{% endif %}>Allow</option>
            </select>
          </div>
        </div>
        <div class="form-actions">
          <button type="submit">Rank Shortlist</button>
        </div>
      </form>
    </div>

    {% if save_notice %}
    <div class="card">
      <strong>{{ save_notice }}</strong>
      <div class="hint">Saved jobs are listed below and will remain available after refresh.</div>
    </div>
    {% endif %}

    {% if saved_leads %}
    <div class="card">
      <h2 style="margin-top:0">Saved shortlist</h2>
      {% for job in saved_leads %}
      <div class="job-card">
        <div>
          <div class="score">{{ job.score }}</div>
          <div class="meta">match score</div>
        </div>
        <div>
          <div>
            <span class="pill {% if job.recommendation == 'APPLY' %}apply{% elif job.recommendation == 'SKIP' %}skip{% else %}review{% endif %}">{{ job.recommendation }}</span>
            <span class="pill">{{ job.status|title }}</span>
            <span class="pill">{{ job.platform }}</span>
          </div>
          <h3 style="margin:8px 0 4px">{{ job.title }}</h3>
          <div class="meta">
            {% if job.company %}{{ job.company }}{% endif %}{% if job.company and job.location %} · {% endif %}{% if job.location %}{{ job.location }}{% endif %}
            {% if job.url %} · <a href="{{ job.url }}" target="_blank" rel="noopener" style="color:var(--accent)">job link</a>{% endif %}
          </div>
          {% if job.matched_terms %}
          <div class="terms">
            {% for term in job.matched_terms[:8] %}<span class="term">{{ term }}</span>{% endfor %}
          </div>
          {% endif %}
          {% if job.reasons %}
          <ul class="meta">
            {% for reason in job.reasons[:3] %}<li>{{ reason }}</li>{% endfor %}
          </ul>
          {% endif %}
          <div class="form-actions">
            <a class="action-link primary" href="{{ url_for('job_workspace', lead_id=job.id) }}">Open workspace</a>
          </div>
        </div>
      </div>
      {% endfor %}
    </div>
    {% endif %}

    {% if ranked_jobs %}
    <div class="card">
      <h2 style="margin-top:0">Latest ranked jobs</h2>
      {% for job in ranked_jobs %}
      <div class="job-card">
        <div>
          <div class="score">{{ job.score }}</div>
          <div class="meta">match score</div>
        </div>
        <div>
          <div>
            <span class="pill {% if job.recommendation == 'APPLY' %}apply{% elif job.recommendation == 'SKIP' %}skip{% else %}review{% endif %}">{{ job.recommendation }}</span>
            <span class="pill">{{ job.detected_role_type|replace('_', ' ')|title }}</span>
            <span class="pill">{{ job.platform }}</span>
          </div>
          <h3 style="margin:8px 0 4px">{{ job.title }}</h3>
          <div class="meta">
            {% if job.company %}{{ job.company }}{% endif %}{% if job.company and job.location %} · {% endif %}{% if job.location %}{{ job.location }}{% endif %}
            {% if job.url %} · <a href="{{ job.url }}" target="_blank" rel="noopener" style="color:var(--accent)">job link</a>{% endif %}
          </div>
          {% if job.positive_signals %}
          <div class="terms">
            {% for term in job.positive_signals %}<span class="term">+ {{ term }}</span>{% endfor %}
          </div>
          {% endif %}
          {% if job.risk_signals %}
          <div class="terms">
            {% for term in job.risk_signals %}<span class="term">risk: {{ term }}</span>{% endfor %}
          </div>
          {% endif %}
          {% if job.matched_terms %}
          <div class="terms">
            {% for term in job.matched_terms %}<span class="term">{{ term }}</span>{% endfor %}
          </div>
          {% endif %}
          {% if job.reasons %}
          <ul class="meta">
            {% for reason in job.reasons %}<li>{{ reason }}</li>{% endfor %}
          </ul>
          {% endif %}
          <div class="form-actions">
            <form method="post" action="{{ url_for('index') }}">
              <input type="hidden" name="job_text" value="{{ job.description }}" />
              <button type="submit" class="primary">Generate application pack</button>
            </form>
          </div>
          <details>
            <summary>View pasted job text</summary>
            <pre>{{ job.description }}</pre>
          </details>
        </div>
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>
</body>
</html>
"""


JOB_WORKSPACE_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Application Workspace</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&family=Space+Grotesk:wght@500;700&display=swap');
    :root { --ink:#e2e8f0; --muted:#94a3b8; --accent:#67e8f9; --panel:#0f172a; --panel-2:#020617; --stroke:#334155; --bg:#030712; }
    * { box-sizing: border-box; }
    body { margin:0; font-family:"DM Sans", sans-serif; color:var(--ink); background:radial-gradient(1000px 600px at 20% 10%, rgba(34,197,94,.16), transparent 40%), radial-gradient(900px 500px at 80% 10%, rgba(6,182,212,.18), transparent 38%), var(--bg); min-height:100vh; }
    .shell { max-width:1100px; margin:0 auto; padding:28px 20px 60px; }
    .header { display:flex; justify-content:space-between; align-items:flex-end; gap:16px; flex-wrap:wrap; margin-bottom:18px; }
    h1,h2,h3 { font-family:"Space Grotesk", sans-serif; }
    h1 { margin:0 0 6px; }
    .hint,.meta { color:var(--muted); font-size:13px; }
    .card { background:var(--panel); border:1px solid var(--stroke); border-radius:16px; padding:18px; margin-bottom:14px; }
    .nav-link,button,.action-link { display:inline-flex; align-items:center; justify-content:center; padding:8px 12px; border-radius:10px; border:1px solid var(--stroke); background:var(--panel-2); color:var(--ink); text-decoration:none; font-size:13px; font-weight:600; cursor:pointer; }
    button,.primary { background:linear-gradient(135deg,#0e7490,#06b6d4); border-color:#0e7490; color:#ecfeff; }
    .pill { display:inline-flex; padding:4px 8px; border-radius:999px; font-size:12px; font-weight:700; margin-right:6px; border:1px solid var(--stroke); }
    .grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
    .terms { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }
    .term { font-size:12px; padding:3px 7px; border-radius:999px; background:var(--panel-2); color:var(--muted); border:1px solid var(--stroke); }
    pre { white-space:pre-wrap; max-height:420px; overflow:auto; background:var(--panel-2); border:1px solid var(--stroke); border-radius:10px; padding:12px; color:var(--ink); }
    .form-actions { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:12px; }
    @media (max-width:860px){ .grid{ grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="shell">
    <div class="header">
      <div>
        <h1>{{ lead.title }}</h1>
        <div class="hint">{% if lead.company %}{{ lead.company }}{% endif %}{% if lead.company and lead.location %} · {% endif %}{% if lead.location %}{{ lead.location }}{% endif %}</div>
        <div class="hint">Score {{ lead.score }} · {{ lead.recommendation }} · {{ lead.status|title }} · {{ lead.platform }}</div>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a class="nav-link" href="{{ url_for('jobs') }}">Job Shortlist</a>
        <a class="nav-link" href="{{ url_for('index') }}">Resume Tool</a>
        <a class="nav-link" href="{{ url_for('dashboard') }}">Dashboard</a>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <h2 style="margin-top:0">Application workspace</h2>
        <div>
          <span class="pill">{{ lead.detected_role_type|replace('_', ' ')|title }}</span>
          <span class="pill">{{ lead.status|title }}</span>
        </div>
        {% if lead.url %}<p><a href="{{ lead.url }}" target="_blank" rel="noopener" style="color:var(--accent)">Open original job post</a></p>{% endif %}
        {% if lead.matched_terms %}
        <h3>Matched terms</h3>
        <div class="terms">{% for term in lead.matched_terms %}<span class="term">{{ term }}</span>{% endfor %}</div>
        {% endif %}
        {% if lead.preference_signals %}
        <h3>Preference signals</h3>
        <div class="terms">{% for term in lead.preference_signals %}<span class="term">{{ term }}</span>{% endfor %}</div>
        {% endif %}
        {% if lead.risk_signals %}
        <h3>Risk signals</h3>
        <div class="terms">{% for term in lead.risk_signals %}<span class="term">{{ term }}</span>{% endfor %}</div>
        {% endif %}
        {% if lead.reasons %}
        <h3>Why this score?</h3>
        <ul class="meta">{% for reason in lead.reasons %}<li>{{ reason }}</li>{% endfor %}</ul>
        {% endif %}
        <form method="post" action="{{ url_for('index') }}" class="form-actions">
          <input type="hidden" name="job_lead_id" value="{{ lead.id }}" />
          <input type="hidden" name="job_text" value="{{ lead.description }}" />
          <button type="submit">Generate application pack</button>
        </form>
      </div>

      <div class="card">
        <h2 style="margin-top:0">Generated files</h2>
        {% if generated_links %}
        <div class="form-actions">
          {% for link in generated_links %}<a class="action-link primary" href="{{ link.href }}" target="_blank" rel="noopener">{{ link.label }}</a>{% endfor %}
        </div>
        {% else %}
        <div class="hint">No application pack generated for this job yet.</div>
        {% endif %}
      </div>
    </div>

    <div class="card">
      <h2 style="margin-top:0">Job description</h2>
      <pre>{{ lead.description }}</pre>
    </div>
  </div>
</body>
</html>
"""


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not supabase_is_configured():
        return Response('Supabase is not configured. Check .env keys.', status=500)
    if current_user():
        return redirect(url_for('index'))

    error_msg = None
    success_msg = None
    next_path = _safe_next_path(request.args.get('next') or request.form.get('next'))
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        if not email or not password:
            error_msg = 'Email and password are required.'
        else:
            try:
                auth_resp = SUPABASE_AUTH_CLIENT.auth.sign_in_with_password(
                    {'email': email, 'password': password}
                )
                user = auth_resp.user
                user_session = auth_resp.session
                if not user or not user_session:
                    raise ValueError('Invalid login response from Supabase.')
                session.clear()
                session['user_id'] = str(user.id)
                session['user_email'] = str(user.email or email)
                session['sb_access_token'] = user_session.access_token
                session['sb_refresh_token'] = user_session.refresh_token
                return redirect(next_path)
            except Exception:
                logging.exception('Login failed')
                error_msg = 'Login failed. Check your email/password and try again.'
    return render_template_string(
        AUTH_PAGE,
        title='Login',
        submit_label='Sign In',
        alt_text='Need an account?',
        alt_link='Create one',
        alt_href=url_for('signup'),
        error_msg=error_msg,
        success_msg=success_msg,
        next_path=next_path,
    )


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if not supabase_is_configured():
        return Response('Supabase is not configured. Check .env keys.', status=500)
    if current_user():
        return redirect(url_for('index'))

    error_msg = None
    success_msg = None
    next_path = _safe_next_path(request.args.get('next') or request.form.get('next'))
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip()
        password = request.form.get('password') or ''
        if not email or not password:
            error_msg = 'Email and password are required.'
        else:
            try:
                signup_resp = SUPABASE_AUTH_CLIENT.auth.sign_up(
                    {'email': email, 'password': password}
                )
                if signup_resp.session and signup_resp.user:
                    session.clear()
                    session['user_id'] = str(signup_resp.user.id)
                    session['user_email'] = str(signup_resp.user.email or email)
                    session['sb_access_token'] = signup_resp.session.access_token
                    session['sb_refresh_token'] = signup_resp.session.refresh_token
                    return redirect(next_path)
                success_msg = 'Signup succeeded. Confirm your email if required, then log in.'
            except Exception:
                logging.exception('Signup failed')
                error_msg = 'Signup failed. Try a stronger password or a different email.'
    return render_template_string(
        AUTH_PAGE,
        title='Sign Up',
        submit_label='Create Account',
        alt_text='Already have an account?',
        alt_link='Log in',
        alt_href=url_for('login'),
        error_msg=error_msg,
        success_msg=success_msg,
        next_path=next_path,
    )


@app.route('/logout')
def logout():
    user = current_user()
    if user and SUPABASE_AUTH_CLIENT:
        try:
            client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
            client.auth.set_session(user['access_token'], user['refresh_token'])
            client.auth.sign_out()
        except Exception:
            logging.exception('Supabase sign_out failed')
    session.clear()
    return redirect(url_for('login'))


@app.route('/jobs', methods=['GET', 'POST'])
@login_required
def jobs():
    job_posts_value = ''
    ranked_jobs = []
    save_notice = ''
    preferred_locations = 'Sydney, Remote'
    role_focus = 'both'
    prefer_junior = True
    avoid_senior = True
    if request.method == 'POST':
        job_posts_value = request.form.get('job_posts', '').strip()
        csv_file = request.files.get('job_csv')
        if csv_file and csv_file.filename:
            csv_text = csv_file.read().decode('utf-8-sig', errors='replace').strip()
            if csv_text:
                job_posts_value = '\n---\n'.join([x for x in (job_posts_value, csv_text) if x])
        preferred_locations = request.form.get('preferred_locations', preferred_locations).strip()
        role_focus = request.form.get('role_focus', role_focus).strip() or 'both'
        prefer_junior = request.form.get('prefer_junior', '1') == '1'
        avoid_senior = request.form.get('avoid_senior', '1') == '1'
        if job_posts_value:
            ranked_jobs = rank_job_postings(
                raw_text=job_posts_value,
                resume_dict=load_resume_json(DEFAULT_RESUME),
                preferences={
                    'preferred_locations': preferred_locations,
                    'role_focus': role_focus,
                    'prefer_junior': prefer_junior,
                    'avoid_senior': avoid_senior,
                },
            )
            saved_count = save_job_leads(ranked_jobs)
            save_notice = f'Saved {saved_count} ranked job lead(s) to your shortlist.'
    saved_leads = get_job_leads(limit=50)
    for item in saved_leads:
        item['created_at'] = format_created_at(item.get('created_at'))
        item['updated_at'] = format_created_at(item.get('updated_at'))
    return render_template_string(
        JOBS_PAGE,
        job_posts_value=job_posts_value,
        ranked_jobs=ranked_jobs,
        saved_leads=saved_leads,
        save_notice=save_notice,
        preferred_locations=preferred_locations,
        role_focus=role_focus,
        prefer_junior=prefer_junior,
        avoid_senior=avoid_senior,
        app_version=get_app_version(),
    )


@app.route('/jobs/<lead_id>')
@login_required
def job_workspace(lead_id: str):
    lead = get_job_lead(lead_id)
    if not lead:
        return Response('Job lead not found.', status=404)
    generated_links = []
    for key, label in (
        ('generated_resume_html', 'Resume HTML'),
        ('generated_resume_pdf', 'Resume PDF'),
        ('generated_cover_letter', 'Cover Letter'),
        ('generated_cover_pdf', 'Cover Letter PDF'),
    ):
        filename = lead.get(key)
        if filename:
            generated_links.append({
                'label': label,
                'href': url_for('download_output', filename=Path(str(filename)).name),
            })
    return render_template_string(
        JOB_WORKSPACE_PAGE,
        lead=lead,
        generated_links=generated_links,
        app_version=get_app_version(),
    )


@app.route('/dashboard')
@login_required
def dashboard():
    user = current_user()
    unlimited = has_unlimited_usage(user)
    monthly_used = get_current_month_generation_count()
    generations = get_generation_history(limit=100)
    for item in generations:
        item['created_at'] = format_created_at(item.get('created_at'))
    return render_template_string(
        DASHBOARD_PAGE,
        user_email=(user or {}).get('email', ''),
        monthly_used=monthly_used,
        monthly_limit='Unlimited' if unlimited else MAX_MONTHLY_GENERATIONS,
        generations=generations,
        app_version=get_app_version(),
    )


@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    html_path = None
    pdf_path = None
    html_url = None
    pdf_url = None
    preview_url = None
    cover_url = None
    cover_pdf_url = None
    cover_preview_url = None
    cover_text = None
    fit = None
    detected_focus = None
    strategy_name = None
    job_text_value = ''
    error_msg = None
    user = current_user()
    unlimited = has_unlimited_usage(user)
    monthly_used = get_current_month_generation_count()
    monthly_limit_display = 'Unlimited' if unlimited else MAX_MONTHLY_GENERATIONS
    if request.method == 'POST':
        try:
            cleanup_old_output_files()
            job_text = request.form.get('job_text', '').strip()
            job_lead_id = request.form.get('job_lead_id', '').strip()
            job_text_value = job_text
            monthly_used = get_current_month_generation_count()
            if not unlimited and monthly_used >= MAX_MONTHLY_GENERATIONS:
                raise ValueError(
                    f'You have reached your monthly generation limit ({MAX_MONTHLY_GENERATIONS}).'
                )
            label = 'Tailored'
            job_label = None
            role_type = 'unknown'
            if job_text:
                resume_text = resume_json_to_text(load_resume_json(DEFAULT_RESUME))
                fit = assess_job_fit(job_text=job_text, resume_text=resume_text)
                classification = classify_job(job_text)
                strategy = choose_resume_strategy(classification)
                detected_focus = classification
                strategy_name = strategy.get('name')
                role_type = (classification or {}).get('primary_category') or 'unknown'
            else:
                raise ValueError('Please paste a job description first.')

            html_path, pdf_path, resume_tagline = generate_resume(
                resume_path=DEFAULT_RESUME,
                template_path=DEFAULT_TEMPLATE,
                job_text=job_text,
                out_dir=OUTPUT_DIR,
                label=label,
                job_label=job_label,
            )
            html_path = str(html_path)
            pdf_path = str(pdf_path)
            resume_html_name = Path(html_path).name
            resume_pdf_name = Path(pdf_path).name
            cover_name = ''
            cover_pdf_name = ''
            html_url = url_for('download_output', filename=resume_html_name)
            pdf_url = url_for('download_output', filename=resume_pdf_name)
            preview_url = url_for('preview_output', filename=resume_html_name)
            try:
                cover_path, cover_pdf_path, cover_text = generate_cover_letter(
                    resume_path=DEFAULT_RESUME,
                    job_text=job_text,
                    out_dir=OUTPUT_DIR,
                    label=label,
                    template_path=DEFAULT_TEMPLATE,
                    tagline=resume_tagline,
                )
                cover_name = Path(cover_path).name
                cover_url = url_for('download_output', filename=cover_name)
                if cover_name.lower().endswith('.html'):
                    cover_preview_url = url_for('preview_output', filename=cover_name)
                if cover_pdf_path:
                    cover_pdf_name = Path(cover_pdf_path).name
                    cover_pdf_url = url_for('download_output', filename=cover_pdf_name)
            except (Exception, SystemExit):
                logging.exception("Cover letter generation skipped")
                cover_text = "Cover letter unavailable. Resume generation completed."
            if job_lead_id:
                record_job_lead_outputs(
                    job_lead_id,
                    resume_html=resume_html_name,
                    resume_pdf=resume_pdf_name,
                    cover_letter=cover_name,
                    cover_pdf=cover_pdf_name,
                )
            record_generation(
                job_title=_extract_job_title(job_text),
                detected_role_type=role_type,
                status='success',
            )
            monthly_used += 1
        except (Exception, SystemExit) as exc:
            logging.exception("Resume generation failed")
            if isinstance(exc, ValueError):
                error_msg = str(exc)
            else:
                error_msg = "Something went wrong. Please try again or check your job description."

    return render_template_string(
        PAGE,
        html_path=html_path,
        pdf_path=pdf_path,
        html_url=html_url,
        pdf_url=pdf_url,
        preview_url=preview_url,
        cover_url=cover_url,
        cover_pdf_url=cover_pdf_url,
        cover_preview_url=cover_preview_url,
        cover_text=cover_text,
        fit=fit,
        detected_focus=detected_focus,
        strategy_name=strategy_name,
        job_text_value=job_text_value,
        error_msg=error_msg,
        app_version=get_app_version(),
        user_email=(user or {}).get('email', ''),
        monthly_used=monthly_used,
        monthly_limit=monthly_limit_display,
    )


@app.route('/outputs/<path:filename>')
@login_required
def download_output(filename: str):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=False)


@app.route('/preview/<path:filename>')
@login_required
def preview_output(filename: str):
    path = Path(OUTPUT_DIR) / filename
    if not path.exists():
        return Response('Not found', status=404)
    html = path.read_text(encoding='utf-8', errors='replace')
    inject = """
<style>
  body { overflow-x: hidden !important; }
  .page { width: 100% !important; max-width: 900px !important; margin: 0 auto !important; padding: 24px 22px !important; }
  @media screen {
    .page { padding: 24px 22px !important; box-shadow: none !important; }
  }
</style>
"""
    if '</head>' in html:
        html = html.replace('</head>', inject + '\n</head>')
    else:
        html = inject + html
    return Response(html, mimetype='text/html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5055, debug=False)  # nosec B104


