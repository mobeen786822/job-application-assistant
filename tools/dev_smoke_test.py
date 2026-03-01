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
    load_resume_json,
)

RESUME_PATH = APP_ROOT / 'assets' / 'resume.json'
TEMPLATE_PATH = APP_ROOT / 'assets' / 'template.html'
OUT_DIR = APP_ROOT / 'outputs'

SOFTWARE_JOB_TEXT = """
Graduate software engineer role focused on full stack delivery.
Requires React, TypeScript, JavaScript, REST APIs, responsive frontend, and backend collaboration.
"""

CYBER_JOB_TEXT = """
Cybersecurity-focused software engineer role.
SOC workflows, SIEM context, incident response, vulnerability remediation, Essential Eight.
"""


def extract_project_titles_from_html(html_text: str, limit: int = 6) -> list[str]:
    m = re.search(
        r'<div class="section section-projects">([\s\S]*?)</div>\s*<div class="section">',
        html_text,
        flags=re.IGNORECASE,
    )
    if not m:
        return []
    block = m.group(1)
    return re.findall(r'<span class="entry-title">(.*?)</span>', block, flags=re.IGNORECASE)[:limit]


def extract_project_first_bullets(html_text: str) -> dict[str, str]:
    out = {}
    section = re.search(
        r'<div class="section section-projects">([\s\S]*?)</div>\s*<div class="section">',
        html_text,
        flags=re.IGNORECASE,
    )
    if not section:
        return out
    block = section.group(1)
    pattern = re.compile(
        r'<span class="entry-title">(.*?)</span>[\s\S]*?<ul>\s*<li>(.*?)</li>',
        flags=re.IGNORECASE,
    )
    for title, bullet in pattern.findall(block):
        clean_title = re.sub(r'<.*?>', '', title).strip()
        clean_bullet = re.sub(r'<.*?>', '', bullet).strip()
        out[clean_title] = clean_bullet
    return out


def assert_non_empty_sections(html_text: str) -> None:
    projects_ok = bool(re.search(r'<div class="section-title">Projects</div>[\s\S]*?<div class="entry">', html_text, flags=re.IGNORECASE))
    education_ok = bool(re.search(r'<div class="section-title">Education</div>[\s\S]*?<div class="entry">', html_text, flags=re.IGNORECASE))
    assert projects_ok, 'Projects section rendered empty'
    assert education_ok, 'Education section rendered empty'


def run_case(label: str, job_text: str) -> None:
    classification = classify_job(job_text)
    strategy = choose_resume_strategy(classification)
    html_path, _, _ = generate_resume(
        resume_path=RESUME_PATH,
        template_path=TEMPLATE_PATH,
        job_text=job_text,
        out_dir=OUT_DIR,
        label=f'json-smoke-{label.lower()}',
    )
    html_text = Path(html_path).read_text(encoding='utf-8', errors='replace')
    assert_non_empty_sections(html_text)

    project_order = [re.sub(r'<.*?>', '', t).strip() for t in extract_project_titles_from_html(html_text)]
    first_bullets = extract_project_first_bullets(html_text)
    print(f'[{label}] strategy={strategy.get("name", "UNKNOWN")}')
    print(f'[{label}] project_order={project_order}')
    print(f'[{label}] first_bullets={first_bullets}')

    cancer_block = extract_project_block(html_text, 'Cancer Awareness Mobile App').lower()
    if cancer_block:
        assert 'firebase' in cancer_block, 'Cancer Awareness project lost Firebase reference'
        assert 'aws' not in cancer_block, 'Cancer Awareness project incorrectly contains AWS'


def extract_project_block(html_text: str, project_title: str) -> str:
    section = re.search(
        r'<div class="section section-projects">([\s\S]*?)</div>\s*<div class="section">',
        html_text,
        flags=re.IGNORECASE,
    )
    if not section:
        return ''
    block = section.group(1)
    idx = block.lower().find(project_title.lower())
    if idx == -1:
        return ''
    next_idx = block.lower().find('<span class="entry-title">', idx + 1)
    if next_idx == -1:
        return block[idx:]
    return block[idx:next_idx]


def main() -> None:
    _ = load_resume_json(RESUME_PATH)
    run_case('SOFTWARE', SOFTWARE_JOB_TEXT)
    run_case('CYBER', CYBER_JOB_TEXT)


if __name__ == '__main__':
    main()
