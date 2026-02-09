import argparse
import os
import re
from datetime import datetime
from pathlib import Path

# Page limit for PDF output (defaults to 2 pages).
MAX_PAGES = int(os.environ.get('RESUME_MAX_PAGES', '2'))

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
        '–': '-',
        '—': '-',
        '·': '-',
        '×': 'x',
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


def render_html(name, headline, contact, summary, education, skills, projects, experience, volunteer, certificates, interests, keywords, style_css, header_html=None):
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

    header_block = header_html if header_html else f"""
<div class="header">
  <h1>{name}</h1>
  <div class="tagline">{tagline}</div>
  <div class="contact-row">{join_contact(contact_items)}</div>
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

{header_block}

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


def render_header_html(name: str, headline: str, contact):
    contact_items = []
    for c in contact:
        if c.startswith('http'):
            label = c.replace('https://', '').replace('http://', '')
            contact_items.append(f'<a href="{c}">{label}</a>')
        elif '@' in c:
            contact_items.append(c)
        else:
            contact_items.append(c)

    def join_contact(items):
        if not items:
            return ''
        parts = []
        for item in items:
            parts.append(item)
        return ' <span>·</span> '.join(parts)

    tagline = headline or ''
    return f"""
<div class="header">
  <h1>{name}</h1>
  <div class="tagline">{tagline}</div>
  <div class="contact-row">{join_contact(contact_items)}</div>
</div>
"""


def extract_template_header(template_text: str) -> str | None:
    start = template_text.find('<div class="header">')
    if start == -1:
        return None
    i = start
    depth = 0
    while i < len(template_text):
        if template_text.startswith('<div', i):
            depth += 1
        elif template_text.startswith('</div>', i):
            depth -= 1
            if depth == 0:
                end = i + len('</div>')
                return template_text[start:end]
        i += 1
    return None


def extract_template_sections(template_text: str) -> list[str]:
    titles = re.findall(
        r'<div\\s+class=\"section-title\"\\s*>\\s*(.*?)\\s*</div>',
        template_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = []
    for t in titles:
        t = re.sub(r'<.*?>', '', t).strip()
        if t:
            if t.lower() == 'additional information':
                continue
            cleaned.append(t)
    return cleaned


def build_sections_from_tailored_text(
    text: str,
    name: str | None = None,
    allowed_sections: list[str] | None = None,
):
    allowed_sections = [s.lower() for s in (allowed_sections or [])]
    allowed_set = set(allowed_sections)

    lines = [normalize_text(l.rstrip()) for l in text.splitlines()]
    sections = []
    current = None
    current_entry = None

    def new_section(title: str):
        nonlocal current, current_entry
        current_entry = None
        current = {
            'title': title,
            'entries': [],
            'bullets': [],
            'paragraphs': [],
            'skills': [],
        }
        sections.append(current)

    def clean_md(s: str) -> str:
        s = re.sub(r'\*\*(.*?)\*\*', r'\1', s)
        s = re.sub(r'__([^_]+)__', r'\1', s)
        return s.strip()

    def looks_like_date(s: str) -> bool:
        return bool(re.search(r'\b\d{2}/\d{4}\b|\b\d{4}\b|\bPresent\b', s, re.IGNORECASE))

    def ensure_section():
        if current is None:
            new_section('Tailored Resume')

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        line = clean_md(line)

        if line.startswith('# '):
            continue
        if line.startswith('## '):
            title = line[3:].strip()
            if name and title.lower() == name.lower():
                continue
            title_key = title.lower()
            if not allowed_set or title_key in allowed_set:
                new_section(title)
            else:
                current = None
            continue
        if line.startswith('### '):
            ensure_section()
            current_entry = {'title': '', 'subtitle': '', 'date': '', 'bullets': []}
            content = line[4:].strip()
            parts = [p.strip() for p in content.split('|')]
            if len(parts) >= 2 and looks_like_date(parts[-1]):
                current_entry['date'] = parts[-1]
                current_entry['title'] = parts[0]
                if len(parts) > 2:
                    current_entry['subtitle'] = ' | '.join(parts[1:-1])
            else:
                current_entry['title'] = parts[0]
                if len(parts) > 1:
                    current_entry['subtitle'] = ' | '.join(parts[1:])
            current['entries'].append(current_entry)
            continue

        if line in ('---', '—', '--'):
            continue

        if line.startswith('- ') or line.startswith('* '):
            ensure_section()
            item = line[2:].strip()
            if current['title'].lower().find('skill') >= 0:
                if ':' in item:
                    item = item.split(':', 1)[1].strip()
                for part in [p.strip() for p in item.split(',')]:
                    if part:
                        current['skills'].append(part)
            else:
                if current_entry is not None:
                    current_entry['bullets'].append(item)
                else:
                    current['bullets'].append(item)
            continue

        if not current and allowed_set:
            continue
        ensure_section()
        if current['title'].lower() == 'education':
            if '|' in line:
                left, right = [p.strip() for p in line.split('|', 1)]
                entry = {'title': '', 'subtitle': '', 'date': right, 'bullets': []}
                if ' - ' in left:
                    entry['title'], entry['subtitle'] = [p.strip() for p in left.split(' - ', 1)]
                else:
                    entry['title'] = left
                current['entries'].append(entry)
                continue
        if current_entry is not None:
            if looks_like_date(line):
                current_entry['date'] = line
            elif not current_entry['subtitle']:
                current_entry['subtitle'] = line
            else:
                current['paragraphs'].append(line)
        else:
            current['paragraphs'].append(line)

    return sections, allowed_sections


def render_sections_to_html(sections, allowed_sections):
    html_parts = []
    preferred_order = allowed_sections[:]
    if not preferred_order:
        preferred_order = [
            'professional summary',
            'key skills / technical skills',
            'key skills',
            'technical skills',
            'professional experience',
            'projects',
            'education',
            'certifications',
            'additional information',
        ]

    def section_sort_key(s):
        title = s['title'].lower()
        if title in preferred_order:
            return (0, preferred_order.index(title))
        return (1, title)

    for section in sorted(sections, key=section_sort_key):
        if section['title'].lower() == 'additional information':
            continue
        html_parts.append('<div class="section">')
        html_parts.append(f'<div class="section-title">{section["title"]}</div>')

        if section['skills']:
            html_parts.append('<div class="skills-grid">')
            for skill in section['skills']:
                html_parts.append(f'<span class="skill-tag">{skill}</span>')
            html_parts.append('</div>')

        for p in section['paragraphs']:
            html_parts.append(f'<p class="summary">{p}</p>')

        for entry in section['entries']:
            html_parts.append('<div class="entry">')
            html_parts.append('<div class="entry-header">')
            html_parts.append(f'<span class="entry-title">{entry.get("title", "")}</span>')
            if entry.get('date'):
                html_parts.append(f'<span class="entry-date">{entry["date"]}</span>')
            html_parts.append('</div>')
            if entry.get('subtitle'):
                html_parts.append(f'<div class="entry-subtitle">{entry["subtitle"]}</div>')
            if entry.get('bullets'):
                html_parts.append('<ul>')
                for b in entry['bullets']:
                    html_parts.append(f'<li>{b}</li>')
                html_parts.append('</ul>')
            html_parts.append('</div>')

        if section['bullets']:
            html_parts.append('<ul>')
            for b in section['bullets']:
                html_parts.append(f'<li>{b}</li>')
            html_parts.append('</ul>')

        html_parts.append('</div>')

    return '\n'.join(html_parts)


def _format_tailored_text_to_html(
    text: str,
    name: str | None = None,
    allowed_sections: list[str] | None = None,
) -> str:
    sections, allowed_sections = build_sections_from_tailored_text(text, name, allowed_sections)
    return render_sections_to_html(sections, allowed_sections)


def _format_tailored_text_to_html(
    text: str,
    name: str | None = None,
    allowed_sections: list[str] | None = None,
) -> str:
    allowed_sections = [s.lower() for s in (allowed_sections or [])]
    allowed_set = set(allowed_sections)
    lines = [normalize_text(l.rstrip()) for l in text.splitlines()]

    sections = []
    current = None
    current_entry = None

    def new_section(title: str):
        nonlocal current, current_entry
        current_entry = None
        current = {
            'title': title,
            'entries': [],
            'bullets': [],
            'paragraphs': [],
            'skills': [],
        }
        sections.append(current)

    def clean_md(s: str) -> str:
        s = re.sub(r'\*\*(.*?)\*\*', r'\1', s)
        s = re.sub(r'__([^_]+)__', r'\1', s)
        return s.strip()

    def looks_like_date(s: str) -> bool:
        return bool(re.search(r'\b\d{2}/\d{4}\b|\b\d{4}\b|\bPresent\b', s, re.IGNORECASE))

    def ensure_section():
        if current is None:
            new_section('Tailored Resume')

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        line = clean_md(line)

        if line.startswith('# '):
            # Top-level title, ignore if it's the name/header.
            continue
        if line.startswith('## '):
            title = line[3:].strip()
            if name and title.lower() == name.lower():
                continue
            title_key = title.lower()
            if not allowed_set or title_key in allowed_set:
                new_section(title)
            else:
                # Skip sections not present in the template.
                current = None
            continue
        if line.startswith('### '):
            ensure_section()
            current_entry = {'title': '', 'subtitle': '', 'date': '', 'bullets': []}
            content = line[4:].strip()
            parts = [p.strip() for p in content.split('|')]
            if len(parts) >= 2 and looks_like_date(parts[-1]):
                current_entry['date'] = parts[-1]
                current_entry['title'] = parts[0]
                if len(parts) > 2:
                    current_entry['subtitle'] = ' | '.join(parts[1:-1])
            else:
                current_entry['title'] = parts[0]
                if len(parts) > 1:
                    current_entry['subtitle'] = ' | '.join(parts[1:])
            current['entries'].append(current_entry)
            continue

        if line in ('---', '—', '--'):
            continue

        if line.startswith('- ') or line.startswith('* '):
            ensure_section()
            item = line[2:].strip()
            if current['title'].lower().find('skill') >= 0:
                # Split skills by commas and ignore category labels.
                if ':' in item:
                    item = item.split(':', 1)[1].strip()
                for part in [p.strip() for p in item.split(',')]:
                    if part:
                        current['skills'].append(part)
            else:
                if current_entry is not None:
                    current_entry['bullets'].append(item)
                else:
                    current['bullets'].append(item)
            continue

        if not current and allowed_set:
            # Drop content outside allowed sections.
            continue
        ensure_section()
        if current['title'].lower() == 'education':
            # Attempt to parse "Title - School | Date"
            if '|' in line:
                left, right = [p.strip() for p in line.split('|', 1)]
                entry = {'title': '', 'subtitle': '', 'date': right, 'bullets': []}
                if ' - ' in left:
                    entry['title'], entry['subtitle'] = [p.strip() for p in left.split(' - ', 1)]
                else:
                    entry['title'] = left
                current['entries'].append(entry)
                continue
        if current_entry is not None:
            if looks_like_date(line):
                current_entry['date'] = line
            elif not current_entry['subtitle']:
                # Treat as subtitle if entry exists and subtitle not set.
                current_entry['subtitle'] = line
            else:
                current['paragraphs'].append(line)
        else:
            current['paragraphs'].append(line)

    html_parts = []
    preferred_order = allowed_sections[:]
    if not preferred_order:
        preferred_order = [
            'professional summary',
            'key skills / technical skills',
            'key skills',
            'technical skills',
            'professional experience',
            'projects',
            'education',
            'certifications',
            'additional information',
        ]

    def section_sort_key(s):
        title = s['title'].lower()
        if title in preferred_order:
            return (0, preferred_order.index(title))
        return (1, title)

    for section in sorted(sections, key=section_sort_key):
        html_parts.append('<div class="section">')
        html_parts.append(f'<div class="section-title">{section["title"]}</div>')

        if section['skills']:
            html_parts.append('<div class="skills-grid">')
            for skill in section['skills']:
                html_parts.append(f'<span class="skill-tag">{skill}</span>')
            html_parts.append('</div>')

        for p in section['paragraphs']:
            html_parts.append(f'<p class="summary">{p}</p>')

        for entry in section['entries']:
            html_parts.append('<div class="entry">')
            html_parts.append('<div class="entry-header">')
            html_parts.append(f'<span class="entry-title">{entry.get("title", "")}</span>')
            if entry.get('date'):
                html_parts.append(f'<span class="entry-date">{entry["date"]}</span>')
            html_parts.append('</div>')
            if entry.get('subtitle'):
                html_parts.append(f'<div class="entry-subtitle">{entry["subtitle"]}</div>')
            if entry.get('bullets'):
                html_parts.append('<ul>')
                for b in entry['bullets']:
                    html_parts.append(f'<li>{b}</li>')
                html_parts.append('</ul>')
            html_parts.append('</div>')

        if section['bullets']:
            html_parts.append('<ul>')
            for b in section['bullets']:
                html_parts.append(f'<li>{b}</li>')
            html_parts.append('</ul>')

        html_parts.append('</div>')

    return '\n'.join(html_parts)


def _extract_tagline(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and lines[0].lower().startswith('tagline:'):
        tagline = lines[0].split(':', 1)[1].strip()
        rest = '\n'.join(lines[1:])
        return tagline, rest
    return None, text


def _validate_tagline(tagline: str, resume_text: str) -> str | None:
    if not tagline:
        return None
    # Enforce short tagline (few words).
    words = re.findall(r'[A-Za-z0-9\+\#\-]+', tagline)
    if len(words) > 6:
        return None
    resume_l = normalize_text(resume_text).lower()
    # Allow common separators and small words.
    stop = {
        'and', 'or', 'for', 'with', 'in', 'on', 'to', 'of', 'the', 'a', 'an',
        'developer', 'engineer', 'analyst', 'specialist'
    }
    tokens = re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', tagline.lower())
    for t in tokens:
        if t in stop or len(t) < 3:
            continue
        if t not in resume_l:
            return None
    return tagline


def generate_tagline_with_openai(job_text: str, resume_text: str) -> str | None:
    try:
        from openai import OpenAI
    except Exception:
        return None
    client = OpenAI()
    model = os.environ.get('OPENAI_MODEL', 'gpt-5.2')
    prompt = (
        "Create a very short, role-specific resume tagline based on the job description and the resume. "
        "Return a single line only, no quotes, no extra text. "
        "Use 3 to 6 words maximum. Avoid separators like '·' or '|'. "
        "STRICT RULE: Use only roles/skills/terms that already appear in the resume text. "
        "Do NOT invent or add new tools, skills, or roles.\n\n"
        f"Job description:\n{job_text}\n\nResume:\n{resume_text}\n"
    )
    resp = client.responses.create(model=model, input=prompt)
    text = getattr(resp, 'output_text', None)
    if text:
        tagline = text.strip().splitlines()[0]
        return _validate_tagline(tagline, resume_text)
    output = getattr(resp, 'output', [])
    if output:
        parts = []
        for item in output:
            content = getattr(item, 'content', []) or (item.get('content', []) if isinstance(item, dict) else [])
            for c in content:
                if getattr(c, 'type', None) == 'output_text':
                    parts.append(c.text)
                elif isinstance(c, dict) and c.get('type') == 'output_text':
                    parts.append(c.get('text', ''))
        if parts:
            tagline = parts[0].strip().splitlines()[0]
            return _validate_tagline(tagline, resume_text)
    return None


def _section_has_content(section) -> bool:
    if section['skills'] or section['bullets'] or section['paragraphs']:
        return True
    for e in section['entries']:
        if e.get('title') or e.get('subtitle') or e.get('date') or e.get('bullets'):
            if e.get('bullets') or e.get('title') or e.get('subtitle') or e.get('date'):
                return True
    return False


def trim_sections_once(sections) -> bool:
    # Returns True if something was removed.
    priority = [
        'additional information',
        'certifications',
        'projects',
        'professional experience',
        'education',
        'key skills / technical skills',
        'key skills',
        'technical skills',
        'professional summary',
    ]

    def find_section(title):
        for s in sections:
            if s['title'].lower() == title:
                return s
        return None

    for title in priority:
        section = find_section(title)
        if not section:
            continue
        # Trim entries' bullets
        if section['entries']:
            for e in reversed(section['entries']):
                if e.get('bullets'):
                    e['bullets'].pop()
                    return True
        # Trim section bullets
        if section['bullets']:
            section['bullets'].pop()
            return True
        # Trim skills
        if section['skills']:
            section['skills'].pop()
            return True
        # Trim paragraphs (keep at least one summary if possible)
        if section['paragraphs'] and not (title == 'professional summary' and len(section['paragraphs']) <= 1):
            section['paragraphs'].pop()
            return True
        # Remove empty section
        if not _section_has_content(section):
            sections.remove(section)
            return True
    return False


def count_pdf_pages(pdf_path: Path) -> int:
    try:
        from PyPDF2 import PdfReader
    except Exception as e:
        raise SystemExit('PyPDF2 is required. Install with: python -m pip install PyPDF2') from e
    reader = PdfReader(str(pdf_path))
    return len(reader.pages)


def _apply_tagline_to_header(header_html: str, tagline: str | None) -> str:
    if not header_html or not tagline:
        return header_html
    start_token = '<div class="tagline">'
    end_token = '</div>'
    start = header_html.find(start_token)
    if start == -1:
        return header_html
    start += len(start_token)
    end = header_html.find(end_token, start)
    if end == -1:
        return header_html
    return header_html[:start] + tagline + header_html[end:]


def tailor_resume_with_openai(
    job_text: str,
    resume_text: str,
    allowed_sections: list[str] | None = None,
    fallback_tagline: str | None = None,
):
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        raise SystemExit('OPENAI_API_KEY is required to use AI tailoring.')

    try:
        from openai import OpenAI
    except Exception as e:
        raise SystemExit('OpenAI SDK required. Install with: python -m pip install openai') from e

    allowed_section_text = "\\n".join(allowed_sections) + "\\n" if allowed_sections else ""
    instructions = (
        "You are a professional resume writer and ATS optimisation expert.\n\n"
        "I will provide you with:\n\n"
        "A job description\n\n"
        "My current resume\n\n"
        "Your task:\n\n"
        "Update my resume so it is tailored specifically to the job description.\n\n"
        "Strict rules (must follow):\n\n"
        "DO NOT invent, exaggerate, or add any new experience, skills, certifications, tools, or qualifications.\n\n"
        "DO NOT claim I have done something that is not already written in my resume.\n\n"
        "You may only rewrite, restructure, reword, reorder, and remove content based on what already exists.\n\n"
        "If something is not relevant to the job description, remove it completely.\n\n"
        "If something is important but buried, move it higher and make it more visible.\n\n"
        "Improve bullet points to sound more achievement-based, but only using the same meaning and information already provided.\n\n"
        "Optimise the resume for ATS keyword matching using wording from the job description, but only when it truthfully matches my existing experience.\n\n"
        "Output requirements:\n\n"
        "Return the updated resume in a clean professional format with these sections (only include sections that apply):\n\n"
        "Professional Summary (tailored to the job)\n\n"
        "Key Skills / Technical Skills\n\n"
        "Professional Experience\n\n"
        "Projects\n\n"
        "Education\n\n"
        "Certifications\n\n"
        "Additional Information (only if relevant)\n\n"
        "Additional formatting rules:\n\n"
        "Keep it concise, modern, and recruiter-friendly.\n\n"
        "Use bullet points.\n\n"
        "Use action verbs.\n\n"
        "Avoid fluff.\n\n"
        "Keep everything aligned to the job description.\n\n"
        "Most important objective:\n\n"
        "Make my resume look like it was written specifically for this job posting, without adding false information.\n\n"
        "Formatting constraints:\n\n"
        "Output plain text only.\n"
        "Start your response with a single line: 'TAGLINE: <short role-specific tagline>'.\n"
        "Use section headers starting with '## ' and ONLY these exact section titles:\n"
        f"{allowed_section_text}"
        "Use entry headers starting with '### '.\n"
        "Use bullet lines starting with '- '.\n"
        "Do not include name/contact at the top.\n"
        "Do not include separators like '---'.\n"
        "Do not include notes, disclaimers, or meta commentary."
    )

    model = os.environ.get('OPENAI_MODEL', 'gpt-5.2')
    client = OpenAI()
    response = client.responses.create(
        model=model,
        instructions=instructions,
        input=(
            "Job description:\n"
            f"{job_text}\n\n"
            "Current resume:\n"
            f"{resume_text}\n"
        ),
    )

    text = getattr(response, 'output_text', None)
    if text:
        text = text.strip()
        tagline, body = _extract_tagline(text)
        if not tagline:
            tagline = generate_tagline_with_openai(job_text=job_text, resume_text=resume_text) or fallback_tagline
        return tagline, body
    output = getattr(response, 'output', [])
    if output:
        parts = []
        for item in output:
            content = getattr(item, 'content', []) or (item.get('content', []) if isinstance(item, dict) else [])
            for c in content:
                if getattr(c, 'type', None) == 'output_text':
                    parts.append(c.text)
                elif isinstance(c, dict) and c.get('type') == 'output_text':
                    parts.append(c.get('text', ''))
        text = '\n'.join([p for p in parts if p]).strip()
        if text:
            tagline, body = _extract_tagline(text)
            if not tagline:
                tagline = generate_tagline_with_openai(job_text=job_text, resume_text=resume_text) or fallback_tagline
            return tagline, body
    raise SystemExit('OpenAI response did not include text output.')


def generate_resume(resume_path, template_path, job_text=None, out_dir=None, label=None, job_label=None):
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
    extra_pdf_css = "\n@media print { .page { padding-top: 6mm; } }\n"
    style_css = style_css + extra_pdf_css

    header_lines, sections = split_sections(resume_text)
    name, contact = parse_header(header_lines)

    headline = ''
    if 'Software Engineer' in sections:
        headline = 'Software Engineer'

    template_header_html = extract_template_header(template_text)
    template_sections = extract_template_sections(template_text)

    ai_sections = None
    ai_allowed_sections = None
    ai_header_html = None

    if os.environ.get('OPENAI_API_KEY') and (job_text or '').strip():
        tagline, tailored_text = tailor_resume_with_openai(
            job_text=job_text,
            resume_text=resume_text,
            allowed_sections=template_sections,
            fallback_tagline=None,
        )
        header_html = template_header_html or render_header_html(name=name, headline=headline, contact=contact)
        if tagline:
            header_html = _apply_tagline_to_header(header_html, tagline)
        sections, allowed_sections = build_sections_from_tailored_text(
            tailored_text,
            name=name,
            allowed_sections=template_sections,
        )
        ai_sections = sections
        ai_allowed_sections = allowed_sections
        ai_header_html = header_html
        html_body = header_html + render_sections_to_html(sections, allowed_sections)
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tailored Resume</title>
<style>
{style_css}
.section-title {{ font-weight: 700; margin-top: 16px; }}
.summary {{ margin: 6px 0; }}
ul {{ margin: 6px 0 12px 18px; }}
</style>
</head>
<body>
<div class="page">
{html_body}
</div>
</body>
</html>
"""
        out_dir = Path(out_dir) if out_dir else (Path(__file__).resolve().parents[1] / 'outputs')
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_label = re.sub(r'[^A-Za-z0-9_-]+', '-', (label or 'Tailored')).strip('-') or 'Tailored'
        base = f"Resume_{safe_label}_{stamp}"
        html_path = out_dir / f"{base}.html"
        pdf_path = out_dir / f"{base}.pdf"
        html_path.write_text(html, encoding='utf-8')
    else:
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

        header_html = template_header_html or render_header_html(name=name, headline=headline, contact=contact)
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
            header_html=header_html,
        )

        out_dir = Path(out_dir) if out_dir else (Path(__file__).resolve().parents[1] / 'outputs')
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_label = re.sub(r'[^A-Za-z0-9_-]+', '-', (label or 'Tailored')).strip('-') or 'Tailored'
        base = f"Resume_{safe_label}_{stamp}"

        html_path = out_dir / f"{base}.html"
        pdf_path = out_dir / f"{base}.pdf"

        html_path.write_text(html, encoding='utf-8')

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise SystemExit('Playwright is required. Install with: python -m pip install playwright && python -m playwright install chromium') from e

    # If AI sections exist, trim to fit within MAX_PAGES (default 2).
    if ai_sections is not None and MAX_PAGES > 0:
        def build_html_from_sections():
            body = ai_header_html + render_sections_to_html(ai_sections, ai_allowed_sections)
            return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tailored Resume</title>
<style>
{style_css}
.section-title {{ font-weight: 700; margin-top: 16px; }}
.summary {{ margin: 6px 0; }}
ul {{ margin: 6px 0 12px 18px; }}
</style>
</head>
<body>
<div class="page">
{body}
</div>
</body>
</html>
"""

        tmp_html = out_dir / f'{base}_tmp.html'
        tmp_pdf = out_dir / f'{base}_tmp.pdf'
        with sync_playwright() as p:
            browser = p.chromium.launch()
            while True:
                html = build_html_from_sections()
                tmp_html.write_text(html, encoding='utf-8')
                page = browser.new_page()
                page.goto(tmp_html.as_uri(), wait_until='networkidle')
                page.pdf(path=str(tmp_pdf), format='A4', print_background=True, margin={
                    'top': '0', 'right': '0', 'bottom': '0', 'left': '0'
                })
                page.close()
                try:
                    pages = count_pdf_pages(tmp_pdf)
                except Exception:
                    pages = MAX_PAGES
                if pages <= MAX_PAGES:
                    html_path.write_text(html, encoding='utf-8')
                    break
                if not trim_sections_once(ai_sections):
                    html_path.write_text(html, encoding='utf-8')
                    break
            browser.close()

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
