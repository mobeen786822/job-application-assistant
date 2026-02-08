
import os
from pathlib import Path
from flask import Flask, request, render_template_string
from tools.resume_bot import generate_resume

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_RESUME = os.environ.get('RESUME_TXT', r'C:\\Users\\mobee\\Downloads\\My Resume (1).txt')
DEFAULT_TEMPLATE = os.environ.get('RESUME_TEMPLATE', r'C:\\Users\\mobee\\Desktop\\Blackmagic CV.html')
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
    body { font-family: system-ui, Arial, sans-serif; margin: 24px; color: #1a1a1a; }
    h1 { font-size: 20px; margin-bottom: 8px; }
    .hint { color: #666; font-size: 13px; margin-bottom: 16px; }
    textarea { width: 100%; min-height: 260px; font-family: ui-monospace, Consolas, monospace; font-size: 13px; padding: 10px; }
    input[type=text] { width: 100%; padding: 8px; font-size: 14px; }
    button { padding: 10px 14px; font-size: 14px; margin-top: 12px; }
    .result { margin-top: 18px; padding: 12px; background: #f6f6f6; border-radius: 6px; }
    .result code { display: block; margin: 6px 0; }
  </style>
</head>
<body>
  <h1>Resume Tailor</h1>
  <div class="hint">Paste a job description and generate a tailored HTML + PDF.</div>
  <form method="post">
    <label>Job title or label (optional)</label>
    <input type="text" name="label" placeholder="e.g. Frontend Engineer - Company" />
    <div style="height:10px"></div>
    <label>Job description</label>
    <textarea name="job_text" placeholder="Paste the full job description here..."></textarea>
    <button type="submit">Generate</button>
  </form>

  {% if html_path %}
  <div class="result">
    <div>Generated files:</div>
    <code>{{ html_path }}</code>
    <code>{{ pdf_path }}</code>
  </div>
  {% endif %}
</body>
</html>
"""


@app.route('/', methods=['GET', 'POST'])
def index():
    html_path = None
    pdf_path = None
    if request.method == 'POST':
        job_text = request.form.get('job_text', '').strip()
        label = request.form.get('label', '').strip() or 'Tailored'
        html_path, pdf_path = generate_resume(
            resume_path=DEFAULT_RESUME,
            template_path=DEFAULT_TEMPLATE,
            job_text=job_text,
            out_dir=OUTPUT_DIR,
            label=label,
        )
        html_path = str(html_path)
        pdf_path = str(pdf_path)
    return render_template_string(PAGE, html_path=html_path, pdf_path=pdf_path)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5055, debug=False)
