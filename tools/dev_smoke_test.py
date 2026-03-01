import re
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tools.resume_bot import (
    choose_resume_strategy,
    classify_job,
    generate_resume,
    generate_summary_with_guard,
    parse_resume_sections,
)

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


def run_summary_guard_smoke(job_text: str) -> None:
    resume_text = RESUME_PATH.read_text(encoding='utf-8', errors='replace')
    classification = classify_job(job_text)
    strategy = choose_resume_strategy(classification)
    summary_data = generate_summary_with_guard(
        job_text=job_text,
        resume_text=resume_text,
        fallback_summary='',
        classification=classification,
        strategy=strategy,
    )
    summary = str(summary_data.get('summary', ''))
    summary_l = summary.lower()
    for token in ('c#', '.net', 'asp.net', 'dotnet'):
        assert token not in summary_l, f'Summary contains banned token: {token}'
    print(f'[SUMMARY] summary={summary}')
    print(f'[SUMMARY] tech_used={summary_data.get("tech_used", [])}')


def _extract_project_block(html_text: str, project_title: str) -> str:
    idx = html_text.lower().find(project_title.lower())
    if idx == -1:
        return ''
    next_idx = html_text.lower().find('<span class="entry-title">', idx + 1)
    if next_idx == -1:
        return html_text[idx:]
    return html_text[idx:next_idx]


def run_project_scope_validation_smoke() -> None:
    resume_text = RESUME_PATH.read_text(encoding='utf-8', errors='replace')
    parsed = parse_resume_sections(resume_text)
    cancer = parsed.get('projects', {}).get('Cancer Awareness Mobile App', {})
    original_block = ' '.join([cancer.get('title', ''), cancer.get('subtitle', ''), ' '.join(cancer.get('bullets', []))])
    original_has_aws = bool(re.search(r'\baws\b', original_block, flags=re.IGNORECASE))

    html_path, _, _ = generate_resume(
        resume_path=RESUME_PATH,
        template_path=TEMPLATE_PATH,
        job_text=CYBER_JOB_TEXT,
        out_dir=OUT_DIR,
        label='smoke-project-scope',
    )
    html_text = Path(html_path).read_text(encoding='utf-8', errors='replace')
    cancer_block = _extract_project_block(html_text, 'Cancer Awareness Mobile App')
    if not original_has_aws:
        assert 'aws' not in cancer_block.lower(), 'Cancer Awareness Mobile App incorrectly contains AWS'
    print(f'[PROJECT_SCOPE] cancer_contains_aws={"aws" in cancer_block.lower()} original_has_aws={original_has_aws}')


def main() -> None:
    run_case('SOFTWARE', SOFTWARE_JOB_TEXT)
    run_case('CYBER', CYBER_JOB_TEXT)
    run_summary_guard_smoke(CYBER_JOB_TEXT)
    run_project_scope_validation_smoke()


if __name__ == '__main__':
    main()
