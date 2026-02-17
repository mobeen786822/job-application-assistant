
import os
import subprocess
from pathlib import Path
from flask import Flask, request, render_template_string, send_from_directory, url_for, Response
from tools.resume_bot import generate_resume, generate_cover_letter, assess_job_fit

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_RESUME = os.environ.get('RESUME_TXT', str(APP_ROOT / 'assets' / 'resume.txt'))
DEFAULT_TEMPLATE = os.environ.get('RESUME_TEMPLATE', str(APP_ROOT / 'assets' / 'template.html'))
OUTPUT_DIR = os.environ.get('RESUME_OUTPUT_DIR', str(APP_ROOT / 'outputs'))

app = Flask(__name__)


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

PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Resume Tailor</title>
  <style>
    :root {
      --ink: #0f172a;
      --muted: #5b6475;
      --accent: #0ea5e9;
      --accent-2: #22c55e;
      --panel: #ffffff;
      --panel-2: #f8fafc;
      --stroke: #e2e8f0;
      --shadow: 0 24px 80px rgba(15, 23, 42, 0.12);
      --bg-base: #f1f5f9;
      --bg-radial-1: #dbeafe;
      --bg-radial-2: #ecfccb;
    }

    body[data-theme="dark"] {
      --ink: #e2e8f0;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-2: #34d399;
      --panel: #0f172a;
      --panel-2: #111827;
      --stroke: #1f2937;
      --shadow: 0 24px 80px rgba(2, 6, 23, 0.55);
      --bg-base: #0b1220;
      --bg-radial-1: #0c2740;
      --bg-radial-2: #1f2a1b;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Space Grotesk", "Sora", "Manrope", system-ui, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1200px 600px at 10% -10%, var(--bg-radial-1) 0%, rgba(219,234,254,0) 60%),
        radial-gradient(1000px 600px at 100% 0%, var(--bg-radial-2) 0%, rgba(236,252,203,0) 55%),
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

    h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.5px; }
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
      background: linear-gradient(135deg, var(--accent), #38bdf8);
      color: #fff;
      cursor: pointer;
    }
    button[disabled] { opacity: 0.6; cursor: not-allowed; }
    .secondary-btn { background: var(--panel-2); color: var(--ink); border: 1px solid var(--stroke); }
    .theme-btn {
      background: var(--panel);
      color: var(--ink);
      border: 1px solid var(--stroke);
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
    }
    .theme-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15);
    }

    .loading { display: none; align-items: center; gap: 10px; margin-top: 12px; font-size: 13px; color: var(--muted); }
    .spinner {
      width: 16px; height: 16px; border: 2px solid #c7d6e2; border-top-color: var(--accent);
      border-radius: 50%; animation: spin 0.9s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .result { margin-top: 16px; padding: 14px; background: var(--panel-2); border-radius: 14px; border: 1px solid var(--stroke); }
    .actions { display: flex; gap: 10px; margin: 8px 0 12px; flex-wrap: wrap; }
    .actions a {
      display: inline-block;
      padding: 8px 12px;
      background: linear-gradient(135deg, var(--accent-2), #86efac);
      color: #052e16;
      text-decoration: none;
      border-radius: 10px;
      font-size: 13px;
      border: 1px solid #bbf7d0;
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
      border: 1px solid #fecaca;
      background: #fef2f2;
      color: #991b1b;
    }

    body[data-theme="dark"] .alert {
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
    .fit-apply { background: #dcfce7; color: #14532d; border-color: #bbf7d0; }
    .fit-maybe { background: #fef9c3; color: #854d0e; border-color: #fde68a; }
    .fit-no { background: #fee2e2; color: #7f1d1d; border-color: #fecaca; }
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
      </div>
      <button type="button" id="theme-toggle" class="theme-btn" aria-pressed="false">
        <span class="theme-dot"></span>
        <span id="theme-label">Dark mode</span>
      </button>
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
  const themeToggle = document.getElementById('theme-toggle');
  const themeLabel = document.getElementById('theme-label');
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

  const applyTheme = (mode) => {
    document.body.setAttribute('data-theme', mode);
    const isDark = mode === 'dark';
    themeToggle.setAttribute('aria-pressed', isDark ? 'true' : 'false');
    themeLabel.textContent = isDark ? 'Light mode' : 'Dark mode';
  };

  const storedTheme = localStorage.getItem('theme');
  if (storedTheme) {
    applyTheme(storedTheme);
  } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    applyTheme('dark');
  } else {
    applyTheme('light');
  }

  themeToggle.addEventListener('click', () => {
    const current = document.body.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    localStorage.setItem('theme', next);
  });
</script>
</html>
"""


@app.route('/', methods=['GET', 'POST'])
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
    job_text_value = ''
    error_msg = None
    if request.method == 'POST':
        try:
            job_text = request.form.get('job_text', '').strip()
            job_text_value = job_text
            label = 'Tailored'
            job_label = None
            if job_text:
                resume_text = Path(DEFAULT_RESUME).read_text(encoding='utf-8', errors='replace')
                fit = assess_job_fit(job_text=job_text, resume_text=resume_text)
            else:
                raise ValueError('Please paste a job description first.')

            html_path, pdf_path = generate_resume(
                resume_path=DEFAULT_RESUME,
                template_path=DEFAULT_TEMPLATE,
                job_text=job_text,
                out_dir=OUTPUT_DIR,
                label=label,
                job_label=job_label,
            )
            html_path = str(html_path)
            pdf_path = str(pdf_path)
            html_url = url_for('download_output', filename=Path(html_path).name)
            pdf_url = url_for('download_output', filename=Path(pdf_path).name)
            preview_url = url_for('preview_output', filename=Path(html_path).name)
            cover_path, cover_pdf_path, cover_text = generate_cover_letter(
                resume_path=DEFAULT_RESUME,
                job_text=job_text,
                out_dir=OUTPUT_DIR,
                label=label,
                template_path=DEFAULT_TEMPLATE,
            )
            cover_name = Path(cover_path).name
            cover_url = url_for('download_output', filename=cover_name)
            if cover_name.lower().endswith('.html'):
                cover_preview_url = url_for('preview_output', filename=cover_name)
            if cover_pdf_path:
                cover_pdf_url = url_for('download_output', filename=Path(cover_pdf_path).name)
        except Exception as e:
            error_msg = f"Something went wrong. {e}"

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
        job_text_value=job_text_value,
        error_msg=error_msg,
        app_version=get_app_version(),
    )


@app.route('/outputs/<path:filename>')
def download_output(filename: str):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=False)


@app.route('/preview/<path:filename>')
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
    app.run(host='0.0.0.0', port=5055, debug=False)


