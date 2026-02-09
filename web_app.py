
import os
from pathlib import Path
from flask import Flask, request, render_template_string, send_from_directory, url_for, Response
from tools.resume_bot import generate_resume

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_RESUME = os.environ.get('RESUME_TXT', str(APP_ROOT / 'assets' / 'resume.txt'))
DEFAULT_TEMPLATE = os.environ.get('RESUME_TEMPLATE', str(APP_ROOT / 'assets' / 'template.html'))
OUTPUT_DIR = os.environ.get('RESUME_OUTPUT_DIR', str(APP_ROOT / 'outputs'))

app = Flask(__name__)

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
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Space Grotesk", "Sora", "Manrope", system-ui, sans-serif;
      color: var(--ink);
      background:
        radial-gradient(1200px 600px at 10% -10%, #dbeafe 0%, rgba(219,234,254,0) 60%),
        radial-gradient(1000px 600px at 100% 0%, #ecfccb 0%, rgba(236,252,203,0) 55%),
        #f1f5f9;
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
      background: #fff;
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
    .preview { width: 100%; height: 780px; border: 1px solid var(--stroke); border-radius: 12px; background: #fff; }

    .grid {
      display: grid;
      grid-template-columns: 1.1fr 1fr;
      gap: 18px;
    }
    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr; }
      .preview { height: 640px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="header">
      <div>
        <h1>Resume Tailor</h1>
        <div class="hint">Paste a job description and generate a tailored HTML + PDF.</div>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <form method="post" id="resume-form">
          <label>Job description</label>
          <textarea name="job_text" placeholder="Paste the full job description here..."></textarea>
          <div class="form-actions">
            <button type="submit" id="generate-btn">Generate</button>
            <button type="button" id="clear-btn" class="secondary-btn">Clear</button>
          </div>
          <div class="loading" id="loading">
            <div class="spinner"></div>
            <div id="loading-text">Generating resume…</div>
          </div>
        </form>
      </div>

      <div class="card">
        {% if html_path %}
        <div class="result" id="result">
          <div class="actions">
            <a href="{{ html_url }}" target="_blank" rel="noopener">Download HTML</a>
            <a href="{{ pdf_url }}" target="_blank" rel="noopener">Download PDF</a>
          </div>
          <iframe class="preview" id="preview" src="{{ preview_url }}"></iframe>
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
  const btn = document.getElementById('generate-btn');
  const clearBtn = document.getElementById('clear-btn');
  const loading = document.getElementById('loading');
  const loadingText = document.getElementById('loading-text');
  const steps = [
    'Parsing job description…',
    'Tailoring resume…',
    'Rendering HTML…',
    'Generating PDF…'
  ];
  let stepIdx = 0;
  let timer = null;

  form.addEventListener('submit', () => {
    const result = document.getElementById('result');
    if (result) {
      result.remove();
    }
    btn.disabled = true;
    loading.style.display = 'flex';
    loadingText.textContent = steps[0];
    timer = setInterval(() => {
      stepIdx = (stepIdx + 1) % steps.length;
      loadingText.textContent = steps[stepIdx];
    }, 1800);
  });

  clearBtn.addEventListener('click', () => {
    form.reset();
    btn.disabled = false;
    loading.style.display = 'none';
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


@app.route('/', methods=['GET', 'POST'])
def index():
    html_path = None
    pdf_path = None
    html_url = None
    pdf_url = None
    preview_url = None
    if request.method == 'POST':
        job_text = request.form.get('job_text', '').strip()
        label = 'Tailored'
        job_label = None
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
    return render_template_string(
        PAGE,
        html_path=html_path,
        pdf_path=pdf_path,
        html_url=html_url,
        pdf_url=pdf_url,
        preview_url=preview_url,
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
