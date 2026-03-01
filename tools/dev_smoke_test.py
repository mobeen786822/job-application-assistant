import re
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tools.resume_bot import choose_resume_strategy, classify_job, generate_resume

RESUME_PATH = APP_ROOT / 'assets' / 'resume.txt'
TEMPLATE_PATH = APP_ROOT / 'assets' / 'template.html'
OUT_DIR = APP_ROOT / 'outputs'


SOFTWARE_JOB_TEXT = """
Graduate Software Engineer role focused on frontend delivery.
Requirements include React, TypeScript, JavaScript, REST APIs, and modern frontend development.
You will collaborate with backend teams and ship production features.
"""

CYBER_JOB_TEXT = """
Cybersecurity Analyst role in SOC operations.
Responsibilities include incident response, SIEM monitoring, vulnerability triage, and Essential Eight uplift.
Experience with threat detection and security operations is preferred.
"""


def extract_project_titles_from_html(html_text: str, limit: int = 5) -> list[str]:
    section_match = re.search(
        r'<div class="section section-projects">([\s\S]*?)</div>\s*<div class="section">',
        html_text,
        flags=re.IGNORECASE,
    )
    if not section_match:
        section_match = re.search(
            r'<div class="section section-projects">([\s\S]*?)</div>\s*</div>\s*</body>',
            html_text,
            flags=re.IGNORECASE,
        )
    if not section_match:
        return []
    block = section_match.group(1)
    titles = re.findall(r'<span class="entry-title">(.*?)</span>', block, flags=re.IGNORECASE)
    cleaned = [re.sub(r'<.*?>', '', t).strip() for t in titles if t.strip()]
    return cleaned[:limit]


def run_case(label: str, job_text: str) -> None:
    classification = classify_job(job_text)
    strategy = choose_resume_strategy(classification)
    html_path, pdf_path, _ = generate_resume(
        resume_path=RESUME_PATH,
        template_path=TEMPLATE_PATH,
        job_text=job_text,
        out_dir=OUT_DIR,
        label=f'smoke-{label.lower()}',
    )
    html_text = Path(html_path).read_text(encoding='utf-8', errors='replace')
    titles = extract_project_titles_from_html(html_text)
    print(f'[{label}] classification={classification["primary_category"]} confidence={classification["confidence"]}')
    print(f'[{label}] strategy={strategy.get("name", "UNKNOWN")}')
    print(f'[{label}] first_projects={titles}')
    print(f'[{label}] html={html_path}')
    print(f'[{label}] pdf={pdf_path}')


def main() -> None:
    run_case('SOFTWARE', SOFTWARE_JOB_TEXT)
    run_case('CYBER', CYBER_JOB_TEXT)


if __name__ == '__main__':
    main()
