# Job Application Assistant

Tailors resumes and cover letters to a job description, scores job fit, and exports recruiter-ready HTML/PDF documents from a web interface.

## Live Site

- http://3.107.22.189

## What It Does

- Accepts a pasted job description in a Flask web app
- Evaluates fit with `APPLY` / `MAYBE` / `NO`, confidence, and requirement gaps
- Generates a tailored resume from structured source data (`assets/resume.txt`) and template styles (`assets/template.html`)
- Generates a tailored cover letter
- Exports both resume and cover letter as HTML and PDF
- Supports AI-driven tailoring with strict no-fabrication rules, with heuristic fallback when AI is unavailable

## Tech Stack

- Backend: Python, Flask
- AI: OpenAI Responses API (`openai` SDK)
- Document generation: HTML/CSS templates + Playwright (Chromium) for PDF rendering
- PDF handling: PyPDF2 (page counting/constraints)
- Deployment: GitHub Actions -> Linux VM service restart (`resume-tailor`)

## Architecture

- `web_app.py`: Flask routes, form handling, orchestration
- `tools/resume_bot.py`: resume parsing, tailoring logic, fit scoring, HTML rendering, PDF generation
- `assets/resume.txt`: canonical resume source content
- `assets/template.html`: visual template for outputs
- `outputs/`: generated artifacts

## Local Setup

Requirements:
- Python 3.10+
- `pip`
- Playwright Chromium

Install:

```powershell
python -m pip install flask openai playwright PyPDF2
python -m playwright install chromium
```

Run:

```powershell
python web_app.py
```

Open:
- http://localhost:5055

## Configuration

- `RESUME_TXT` (default: `assets/resume.txt`)
- `RESUME_TEMPLATE` (default: `assets/template.html`)
- `RESUME_OUTPUT_DIR` (default: `outputs`)
- `RESUME_MAX_PAGES` (default: `2`)
- `OPENAI_API_KEY` (enables AI tailoring + AI fit assessment + AI cover letter)
- `OPENAI_MODEL` (default: `gpt-5.2`)
- `APP_VERSION` (optional UI build label)

## CLI Example

```powershell
python tools/resume_bot.py --resume assets/resume.txt --template assets/template.html --job job.txt --out-dir outputs
```

## Deployment

- CI/CD workflow: `.github/workflows/deploy-vm.yml`
- Trigger: push to `main`
- Action: SSH to VM, hard reset to `origin/main`, restart `resume-tailor`

Required secret:
- `VM_SSH_KEY` (repository secret)
