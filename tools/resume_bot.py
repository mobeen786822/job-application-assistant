import argparse
import re
from datetime import datetime
from pathlib import Path

STOPWORDS = {
    'the','and','a','an','to','of','in','for','with','on','at','by','from','as','is','are','be','this',
    'that','it','or','we','you','your','our','their','they','i','me','my','us','will','can','may','must',
    'should','could','would','role','position','team','work','working','experience','skills','ability','strong',
}

DASH_LINE = re.compile(r'^-\s*-\s*-\s*[-\s]*$')
DATE_LINE = re.compile(r'\b\d{2}/\d{4}\s*-\s*(Present|\d{2}/\d{4})\b', re.IGNORECASE)


def normalize_text(text: str) -> str:
    # Fix common mojibake and replace non-ASCII punctuation with ASCII.
    replacements = {
        'â€“': '-',
        'â€”': '-',
        'Â·': '-',
        'Ã—': 'x',
        '\u2013': '-',
        '\u2014': '-',
        '\u2022': '-',
        '\u00b7': '-',
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    # Strip stray control characters
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text


def split_sections(text: str):
    lines = [normalize_text(l.rstrip()) for l in text.splitlines()]
    sections = {}
    header_block = []
    current = None
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            if current is None:
                header_block.append('')
            else:
                sections[current].append('')
            i += 1
            continue

        if DASH_LINE.match(stripped):
            i += 1
            continue

        # Header is a non-empty line followed by a dashed line (ignoring blanks)
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        is_header_candidate = not re.search(r'https?://|@', stripped)
        if is_header_candidate and j < len(lines) and DASH_LINE.match(lines[j].strip()):
            current = stripped
            sections.setdefault(current, [])
            i = j + 1
            continue

        if current is None:
            header_block.append(stripped)
        else:
            sections[current].append(stripped)
        i += 1

    return header_block, sections


def parse_header(header_lines):
    header_lines = [l for l in header_lines if l.strip()]
    name = header_lines[0] if header_lines else ''
    contact = []
    for line in header_lines[1:]:
        if 'x-t-c2-color:' in line:
            line = line.split('x-t-c2-color:')[-1].strip()
        if line:
            contact.append(line)
    return name, contact


def split_entries(block_lines):
    entries = []
    current = []
    for line in block_lines:
        if not line.strip():
            if current:
                entries.append(current)
                current = []
            continue
        current.append(line)
    if current:
        entries.append(current)
    return entries


def parse_education(block_lines):
    entries = []
    for lines in split_entries(block_lines):
        if not lines:
            continue
        title = lines[0]
        school = lines[1] if len(lines) > 1 else ''
        date = lines[2] if len(lines) > 2 else ''
        bullets = []
        for line in lines[3:]:
            if line.lower().startswith('courses'):
                continue
            if line.startswith('-'):
                bullets.append(line.lstrip('-').strip())
        entries.append({
            'title': title,
            'school': school,
            'date': date,
            'bullets': bullets,
        })
    return entries


def parse_experience(block_lines):
    entries = []
    for lines in split_entries(block_lines):
        if not lines:
            continue
        title = lines[0]
        date = ''
        idx = 1
        if len(lines) > 1 and DATE_LINE.search(lines[1]):
            date = lines[1]
            idx = 2
        bullets = []
        for line in lines[idx:]:
            if line.startswith('-'):
                bullets.append(line.lstrip('-').strip())
        entries.append({
            'title': title,
            'date': date,
            'bullets': bullets,
            'raw': ' '.join(lines),
        })
    return entries


def parse_skills(block_lines):
    skills = []
    for line in block_lines:
        if line.startswith('-'):
            line = line.lstrip('-').strip()
        for part in re.split(r'[\|,]', line):
            part = part.strip()
            if part:
                skills.append(part)
    # De-dup preserve order
    seen = set()
    out = []
    for s in skills:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def parse_list(block_lines):
    items = []
    for line in block_lines:
        if line.startswith('-'):
            line = line.lstrip('-').strip()
        if line:
            items.append(line)
    return items


def extract_keywords(job_text, skills):
    if not job_text:
        return []
    job_text_l = normalize_text(job_text).lower()
    matched = [s for s in skills if s.lower() in job_text_l]

    words = re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', job_text_l)
    freq = {}
    for w in words:
        if w in STOPWORDS or len(w) < 3:
            continue
        freq[w] = freq.get(w, 0) + 1
    top = [w for w, _ in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:8]]

    keywords = []
    for s in matched:
        if s not in keywords:
            keywords.append(s)
    for w in top:
        if w not in keywords:
            keywords.append(w)
    return keywords


def relevance_score(text, keywords):
    if not keywords:
        return 0
    t = text.lower()
    return sum(1 for k in keywords if k.lower() in t)


def render_html(name, headline, contact, summary, education, skills, projects, experience, volunteer, certificates, interests, keywords, style_css):
    contact_items = []
    for c in contact:
        if c.startswith('http'):
            label = c.replace('https://', '').replace('http://', '')
            contact_items.append(f'<a href="{c}">{label}</a>')
        elif '@' in c:
            contact_items.append(c)
        else:
            contact_items.append(c)

    tagline = headline
    if keywords:
        extra = ', '.join(keywords[:3])
        tagline = f'{headline} - {extra}' if headline else extra

    summary_text = summary
    if summary_text and summary_text[-1] not in '.!?':
        summary_text += '.'
    if keywords:
        summary_text = summary_text.rstrip('. ') + f". Relevant focus: {', '.join(keywords[:4])}."

    def join_contact(items):
        if not items:
            return ''
        parts = []
        for i, item in enumerate(items):
            parts.append(item)
        return ' <span>-</span> '.join(parts)

    def render_entries(entries, with_subtitle=False):
        html = []
        for e in entries:
            html.append('<div class="entry">')
            html.append('<div class="entry-header">')
            html.append(f'<span class="entry-title">{e.get("title", "")}</span>')
            if e.get('date'):
                html.append(f'<span class="entry-date">{e["date"]}</span>')
            html.append('</div>')
            if with_subtitle and e.get('school'):
                html.append(f'<div class="entry-subtitle">{e["school"]}</div>')
            if e.get('bullets'):
                html.append('<ul>')
                for b in e['bullets']:
                    html.append(f'<li>{b}</li>')
                html.append('</ul>')
            html.append('</div>')
        return '\n'.join(html)

    edu_html = render_entries(education, with_subtitle=True)

    skills_html = ''.join([f'<span class="skill-tag">{s}</span>' for s in skills])

    proj_html = render_entries(projects)
    exp_html = render_entries(experience)
    vol_html = render_entries(volunteer)

    cert_html = ''
    if certificates:
        items = ''.join([f'<li>{c}</li>' for c in certificates])
        cert_html = f"""
<div class=\"section\">
  <div class=\"section-title\">Certificates</div>
  <ul>{items}</ul>
</div>
"""

    interests_html = ''
    if interests:
        interests_html = '<div class="section"><div class="section-title">Interests</div><p class="summary">' + ' - '.join(interests) + '</p></div>'

    volunteer_section = ''
    if vol_html:
        volunteer_section = f"""
<div class=\"section\">
  <div class=\"section-title\">Volunteer Experience</div>
  {vol_html}
</div>
"""

    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"UTF-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
<title>{name} - Resume</title>
<style>
{style_css}
</style>
</head>

<body>
<div class=\"page\">

<div class=\"header\">
  <h1>{name}</h1>
  <div class=\"tagline\">{tagline}</div>
  <div class=\"contact-row\">{join_contact(contact_items)}</div>
</div>

<div class=\"section\">
  <div class=\"section-title\">Professional Summary</div>
  <p class=\"summary\">{summary_text}</p>
</div>

<div class=\"section\">
  <div class=\"section-title\">Education</div>
  {edu_html}
</div>

<div class=\"section\">
  <div class=\"section-title\">Technical Skills</div>
  <div class=\"skills-grid\">{skills_html}</div>
</div>

<div class=\"section\">
  <div class=\"section-title\">Projects</div>
  {proj_html}
</div>

<div class=\"section\">
  <div class=\"section-title\">Experience</div>
  {exp_html}
</div>

{volunteer_section}
{cert_html}
{interests_html}

</div>
</body>
</html>
"""
    return html


def generate_resume(resume_path, template_path, job_text=None, out_dir=None, label=None):
    resume_path = Path(resume_path)
    template_path = Path(template_path)
    if not resume_path.exists():
        raise SystemExit(f'Resume file not found: {resume_path}')
    if not template_path.exists():
        raise SystemExit(f'Template file not found: {template_path}')

    resume_text = resume_path.read_text(encoding='utf-8', errors='replace')
    template_text = template_path.read_text(encoding='utf-8', errors='replace')
    style_match = re.search(r'<style>(.*?)</style>', template_text, re.DOTALL | re.IGNORECASE)
    if style_match:
        style_css = style_match.group(1).strip()
    else:
        style_css = ''
    header_lines, sections = split_sections(resume_text)
    name, contact = parse_header(header_lines)

    headline = ''
    if 'Software Engineer' in sections:
        headline = 'Software Engineer'

    summary_lines = sections.get('Software Engineer', [])
    summary = ' '.join([l for l in summary_lines if l.strip()])

    education = parse_education(sections.get('Education', []))

    skills = parse_skills(sections.get('Skills', []))

    work_entries = parse_experience(sections.get('Work experience/Projects', []))
    volunteer_entries = parse_experience(sections.get('Volunteer Experience', []))

    # Split projects vs experience based on title keywords
    projects = []
    experience = []
    for e in work_entries:
        title_l = e['title'].lower()
        if 'independent contractor' in title_l or 'web developer' in title_l or 'driver' in title_l:
            experience.append(e)
        else:
            projects.append(e)

    certificates = parse_list(sections.get('Certificates', []))
    interests = parse_list(sections.get('Interests', []))

    keywords = extract_keywords(job_text or '', skills)

    # Reorder skills by relevance
    if keywords:
        skills = sorted(skills, key=lambda s: (s.lower() not in [k.lower() for k in keywords], s.lower()))

        # Reorder projects and experience by relevance
        projects = sorted(projects, key=lambda e: -relevance_score(e.get('raw', ''), keywords))
        experience = sorted(experience, key=lambda e: -relevance_score(e.get('raw', ''), keywords))

    # Use first sentence as summary fallback
    if not summary:
        summary = 'Software engineer with a strong foundation in web technologies, networking, and object-oriented programming.'

    html = render_html(
        name=name,
        headline=headline,
        contact=contact,
        summary=summary,
        education=education,
        skills=skills,
        projects=projects,
        experience=experience,
        volunteer=volunteer_entries,
        certificates=certificates,
        interests=interests,
        keywords=keywords,
        style_css=style_css,
    )

    out_dir = Path(out_dir) if out_dir else (Path(__file__).resolve().parents[1] / 'outputs')
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_label = re.sub(r'[^A-Za-z0-9_-]+', '-', (label or 'Tailored')).strip('-') or 'Tailored'
    base = f'Resume_{safe_label}_{stamp}'

    html_path = out_dir / f'{base}.html'
    pdf_path = out_dir / f'{base}.pdf'

    html_path.write_text(html, encoding='utf-8')

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise SystemExit('Playwright is required. Install with: python -m pip install playwright && python -m playwright install chromium') from e

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.as_uri(), wait_until='networkidle')
        page.pdf(path=str(pdf_path), format='A4', print_background=True, margin={
            'top': '0', 'right': '0', 'bottom': '0', 'left': '0'
        })
        browser.close()

    return html_path, pdf_path


def main():
    parser = argparse.ArgumentParser(description='Tailor resume and generate HTML/PDF.')
    parser.add_argument('--resume', required=True, help='Path to resume txt file')
    parser.add_argument('--template', required=True, help='Path to base HTML template')
    parser.add_argument('--job', help='Path to job description text file')
    parser.add_argument('--out-dir', help='Output directory')
    parser.add_argument('--label', help='Optional label for output filename')
    args = parser.parse_args()

    job_text = ''
    if args.job:
        job_path = Path(args.job)
        if not job_path.exists():
            raise SystemExit(f'Job description file not found: {job_path}')
        job_text = job_path.read_text(encoding='utf-8', errors='replace')

    html_path, pdf_path = generate_resume(
        resume_path=args.resume,
        template_path=args.template,
        job_text=job_text,
        out_dir=args.out_dir,
        label=(Path(args.job).stem if args.job else args.label),
    )

    print(f'Wrote HTML: {html_path}')
    print(f'Wrote PDF:  {pdf_path}')


if __name__ == '__main__':
    main()
