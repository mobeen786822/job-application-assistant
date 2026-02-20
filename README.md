# Job Application Assistant

Tailors a resume and cover letter to a pasted job description, then renders recruiter-ready HTML and PDF outputs.

## Features

- Web UI (`Flask`) for pasting a job description and generating tailored documents
- Job-fit assessment with recommendation (`APPLY`, `MAYBE`, `NO`) and gap breakdown
- Resume generation from `assets/resume.txt` + `assets/template.html`
- Cover letter generation with HTML preview (or text fallback)
- CLI support for resume generation
- Optional AI tailoring when `OPENAI_API_KEY` is set, with heuristic fallback when unavailable

## Project Structure

- `web_app.py` - Flask web app (default port `5055`)
- `tools/resume_bot.py` - core parsing, fit scoring, tailoring, and rendering logic
- `assets/resume.txt` - source resume text
- `assets/template.html` - base visual template/style
- `outputs/` - generated files (created automatically)

## Requirements

- Python 3.10+
- `pip`
- Playwright Chromium (for PDF rendering)

Install dependencies:

```powershell
python -m pip install flask openai playwright PyPDF2
python -m playwright install chromium
```

## Run the Web App

```powershell
python web_app.py
```

Open:

- `http://localhost:5055`

## Environment Variables

- `RESUME_TXT` - path to source resume text  
  Default: `assets/resume.txt`
- `RESUME_TEMPLATE` - path to HTML template  
  Default: `assets/template.html`
- `RESUME_OUTPUT_DIR` - output directory  
  Default: `outputs`
- `RESUME_MAX_PAGES` - max pages for AI-tailored resume PDF  
  Default: `2`
- `OPENAI_API_KEY` - enables AI tailoring, fit assessment, and AI cover letters
- `OPENAI_MODEL` - model name used by OpenAI calls  
  Default: `gpt-5.2`
- `APP_VERSION` - optional build/version label shown in UI

## CLI Usage

Generate a tailored resume from files:

```powershell
python tools/resume_bot.py --resume assets/resume.txt --template assets/template.html --job job.txt --out-dir outputs
```

Arguments:

- `--resume` (required): path to resume `.txt`
- `--template` (required): path to template `.html`
- `--job` (optional): path to job description text file
- `--out-dir` (optional): output folder
- `--label` (optional): filename label

## Output Files

Generated in `outputs/` with timestamped names:

- `Resume_<Label>_<Timestamp>.html`
- `Resume_<Label>_<Timestamp>.pdf`
- `CoverLetter_<Label>_<Timestamp>.html` (or `.txt` fallback)
- `CoverLetter_<Label>_<Timestamp>.pdf` (when HTML render succeeds)

## Notes

- Without `OPENAI_API_KEY`, the app still works using heuristic fit scoring and non-AI resume tailoring.
- PDF export depends on Playwright Chromium being installed.

## Auto Deploy (GitHub -> VM)

This repo includes `.github/workflows/deploy-vm.yml` to auto-deploy on every push to `main`.

Required GitHub repository secrets:

- `VM_HOST` - VM public IP or DNS (for example `3.107.22.189`)
- `VM_USER` - SSH user (for example `ubuntu`)
- `VM_SSH_KEY` - private SSH key content (PEM) used to access the VM
- `VM_PORT` - optional SSH port (defaults to `22` if omitted)

Deployment behavior:

- Connects to VM over SSH
- Runs `git fetch`, `git checkout main`, `git reset --hard origin/main`
- Restarts `resume-tailor` service
