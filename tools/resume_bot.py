import argparse
import difflib
import html
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

# Page limit for PDF output (defaults to 2 pages).
def _read_max_pages() -> int:
    raw = (os.environ.get('RESUME_MAX_PAGES', '2') or '2').strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 2
    return value if value > 0 else 2


MAX_PAGES = _read_max_pages()

STOPWORDS = {
    'the','and','a','an','to','of','in','for','with','on','at','by','from','as','is','are','be','this',
    'that','it','or','we','you','your','our','their','they','i','me','my','us','will','can','may','must',
    'should','could','would','role','position','team','work','working','experience','skills','ability','strong',
}

CYBER_TOOL_TERMS = [
    'kali linux',
    'nmap',
    'metasploit',
    'wireshark',
    'burp suite',
    'penetration testing',
    'vulnerability assessment',
    'incident response',
    'threat intelligence',
    'network segmentation',
    'log analysis',
    'firewall configuration',
]

CYBER_JOB_TERMS = [
    'cyber',
    'security',
    'soc',
    'siem',
    'incident',
    'threat',
    'vulnerability',
    'penetration',
    'firewall',
    'acsc',
    'essential eight',
]

DASH_LINE = re.compile(r'^(?:-\s*-\s*-\s*[-\s]*|-{3,})$')
DATE_LINE = re.compile(r'\b\d{2}/\d{4}\s*-\s*(Present|\d{2}/\d{4})\b', re.IGNORECASE)
EXCLUDED_PROJECT_TITLES = {
    'seo-optimised blog posts for ecommerce',
    'ai image generator',
    'ai essay writer',
    'cartoon yourself',
}
URL_PATTERN = re.compile(r'((?:https?://|www\.)[^\s<>"\']+)', re.IGNORECASE)

TAILORED_SECTION_TITLES = [
    'Professional Summary',
    'Key Skills / Technical Skills',
    'Professional Experience',
    'Projects',
    'Education',
    'Certifications',
    'Additional Information',
]

DEFAULT_TAILORED_TAGLINE = (
    'Cybersecurity-Focused Software Engineer | Full-Stack Development | '
    'ACSC Essential Eight | Production Systems'
)


_JOB_FIT_CACHE: dict[str, dict] = {}
_JOB_CLASSIFICATION_CACHE: dict[str, dict] = {}

VALID_PRIMARY_CATEGORIES = {
    'software_engineering',
    'cybersecurity',
    'frontend',
    'backend',
    'devops',
    'unknown',
}
VALID_TONES = {'corporate', 'startup', 'security-heavy', 'unknown'}

DEFAULT_SECTION_ORDER = [
    'Professional Summary',
    'Key Skills / Technical Skills',
    'Professional Experience',
    'Projects',
    'Education',
    'Certifications',
]
SUMMARY_BANNED_RE = re.compile(r"\b(c#|c\s*sharp|\.net|asp\.?net|dotnet)\b", re.IGNORECASE)
GENERIC_BULLET_TERMS = {
    'built', 'developed', 'implemented', 'designed', 'integrated', 'added', 'using', 'with',
    'application', 'platform', 'system', 'workflow', 'features', 'support', 'project', 'web',
    'mobile', 'frontend', 'backend', 'api', 'testing', 'security', 'monitoring', 'deployment',
}

SKILL_GROUP_KEYS = ['languages', 'frontend', 'backend', 'testing', 'security', 'tools']
KEYWORD_TIER_WEIGHTS = {
    # Tier 1 (+3)
    'react': 3,
    'typescript': 3,
    'api': 3,
    'security': 3,
    'incident': 3,
    'monitoring': 3,
    # Tier 2 (+2)
    'validation': 2,
    'lifecycle': 2,
    'deployment': 2,
    'authentication': 2,
    'sla': 2,
    'runbook': 2,
    # Tier 3 (+1)
    'ui': 1,
    'responsive': 1,
    'testing': 1,
    'preview': 1,
    'export': 1,
}
SKILL_SYNONYM_MAP = {
    'REST API Integration': ['rest api', 'rest api integration', 'rest apis', 'api integration'],
    'Jest': ['testing', 'unit testing'],
    'React Testing Library': ['testing', 'react testing library', 'unit testing'],
    'Git': ['version control', 'git'],
    'CI/CD': ['github actions', 'pipeline', 'ci cd'],
    'NoSQL': ['nosql'],
    'Cloud Firestore': ['firestore'],
}

SOFTWARE_BASELINE_SKILLS = [
    'JavaScript',
    'TypeScript',
    'React',
    'Git',
    'REST API Integration',
]

APPSEC_DEVSECOPS_ROLE_TERMS = [
    'appsec',
    'application security',
    'security engineer',
    'devsecops',
    'penetration testing',
    'vulnerability',
    'sast',
    'dast',
]
SOFTWARE_FULLSTACK_ROLE_TERMS = [
    'software engineer',
    'full-stack',
    'full stack',
    'frontend',
    'backend',
    'developer',
    'react',
    'python',
    'api',
]
JUNIOR_GRADUATE_ROLE_TERMS = [
    'graduate',
    'new grad',
    'graduate developer',
    'graduate software engineer',
]
APPSEC_PRIORITY_TAGS = {'security', 'ci cd', 'ci-cd', 'devsecops', 'remediation'}
APPSEC_DEPRIORITY_TAGS = {'ui', 'ux', 'marketing', 'mobile'}
SWE_PRIORITY_TAGS = {'full stack', 'full-stack', 'architecture', 'ai', 'deployment'}
SWE_SECURITY_SUPPORT_TAGS = {'security', 'ci cd', 'ci-cd', 'devsecops', 'sast', 'dast', 'pipeline'}
PENTEST_ROLE_TERMS = [
    'penetration testing',
    'penetration tester',
    'pentest',
    'red team',
    'ethical hacker',
    'offensive security',
    'vulnerability assessment',
]
MOBILE_ROLE_TERMS = [
    'mobile developer',
    'mobile engineer',
    'react native',
    'ios',
    'android',
]
EXPLICIT_MOBILE_REQUIREMENT_TERMS = [
    'react native',
    'mobile development',
    'mobile developer',
    'mobile engineer',
    'cross-platform apps',
    'cross platform apps',
    'cross-platform',
    'cross platform',
    'ios',
    'android',
]


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


def clean_skill_token(skill: str) -> str:
    token = normalize_text(skill).strip()
    # Strip outer-wrapping brackets ONLY when the whole token is wrapped:
    # "(React Native)" -> "React Native", but "React Native (iOS)" stays unchanged.
    if len(token) >= 2 and token[0] == '(' and token[-1] == ')':
        inner = token[1:-1].strip()
        if inner:
            token = inner
    elif len(token) >= 2 and token[0] == '[' and token[-1] == ']':
        inner = token[1:-1].strip()
        if inner:
            token = inner
    token = token.strip(' -;:,.')
    # Balance unmatched open parens: "AWS (EC2, S3" -> "AWS (EC2, S3)"
    open_count = token.count('(')
    close_count = token.count(')')
    if open_count > close_count:
        token = token + ')' * (open_count - close_count)
    elif close_count > open_count:
        # Strip excess trailing close parens: "CloudWatch)" -> "CloudWatch"
        excess = close_count - open_count
        while excess > 0 and token.endswith(')'):
            token = token[:-1]
            excess -= 1
    return token.strip()


def normalize_bullet_object(bullet) -> dict:
    if isinstance(bullet, dict):
        text = normalize_text(str(bullet.get('text', ''))).strip()
        core = bool(bullet.get('core', False))
        try:
            importance = int(bullet.get('importance', 0))
        except Exception:
            importance = 0
        importance = max(0, min(3, importance))
        tags = []
        for raw in bullet.get('tags', []) if isinstance(bullet.get('tags', []), list) else []:
            tag = _normalize_term(str(raw))
            if tag and tag not in tags:
                tags.append(tag)
        return {'text': text, 'core': core, 'importance': importance, 'tags': tags}
    text = normalize_text(str(bullet or '')).strip()
    return {'text': text, 'core': False, 'importance': 0, 'tags': []}


def normalize_bullet_list(bullets) -> list[dict]:
    out = []
    for bullet in bullets or []:
        item = normalize_bullet_object(bullet)
        if item.get('text'):
            out.append(item)
    return out


def bullet_texts(bullets) -> list[str]:
    return [b.get('text', '') for b in normalize_bullet_list(bullets) if b.get('text', '')]


def _split_skills_csv(text: str) -> list[str]:
    """Split a comma-separated skill list respecting parentheses.

    'AWS (EC2, S3, IAM), TypeScript' -> ['AWS (EC2, S3, IAM)', 'TypeScript']
    """
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in text:
        if ch == '(':
            depth += 1
            buf.append(ch)
        elif ch == ')':
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == ',' and depth == 0:
            part = ''.join(buf).strip()
            if part:
                parts.append(part)
            buf = []
        else:
            buf.append(ch)
    if buf:
        part = ''.join(buf).strip()
        if part:
            parts.append(part)
    return parts


def linkify_text(text: str) -> str:
    if not text:
        return ''
    out = []
    last = 0
    for match in URL_PATTERN.finditer(text):
        start, end = match.span()
        out.append(html.escape(text[last:start]))
        raw = match.group(1)
        trailing = ''
        while raw and raw[-1] in '.,);:!?':
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        if not raw:
            out.append(html.escape(match.group(1)))
            last = end
            continue
        href = raw if raw.lower().startswith(('http://', 'https://')) else f'https://{raw}'
        out.append(
            f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">{html.escape(raw)}</a>'
        )
        if trailing:
            out.append(html.escape(trailing))
        last = end
    out.append(html.escape(text[last:]))
    return ''.join(out)


def linkify_text_compact_links(text: str) -> str:
    if not text:
        return ''
    out = []
    last = 0
    for match in URL_PATTERN.finditer(text):
        start, end = match.span()
        out.append(html.escape(text[last:start]))
        raw = match.group(1)
        trailing = ''
        while raw and raw[-1] in '.,);:!?':
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        if not raw:
            out.append(html.escape(match.group(1)))
            last = end
            continue
        href = raw if raw.lower().startswith(('http://', 'https://')) else f'https://{raw}'
        raw_l = raw.lower()
        if 'github.com/' in raw_l:
            icon = '&#x1F419;'
            label = 'Github Repo'
        elif 'github.com' in raw_l:
            icon = '&#x1F419;'
            label = 'Github'
        elif 'linkedin.com' in raw_l:
            icon = '&#x1F4BC;'
            label = 'LinkedIn'
        elif 'onrender.com' in raw_l:
            icon = ''
            label = 'Live demo'
        else:
            icon = ''
            label = raw
        if icon:
            out.append(f'{icon} ')
        out.append(f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">{html.escape(label)}</a>')
        if trailing:
            out.append(html.escape(trailing))
        last = end
    out.append(html.escape(text[last:]))
    return ''.join(out)


def split_sections(text: str):
    lines = [normalize_text(l.rstrip()) for l in text.splitlines()]
    sections = {}
    header_block = []
    current = None
    i = 0
    prev_was_dash = False
    using_new_style_headers = False

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
            prev_was_dash = True
            i += 1
            continue

        if prev_was_dash:
            is_header_candidate = not re.search(r'https?://|@', stripped)
            if is_header_candidate:
                current = stripped
                sections.setdefault(current, [])
                using_new_style_headers = True
                prev_was_dash = False
                i += 1
                continue

        # Header is a non-empty line followed by a dashed line (ignoring blanks)
        if not using_new_style_headers:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            is_header_candidate = not re.search(r'https?://|@', stripped)
            if is_header_candidate and j < len(lines) and DASH_LINE.match(lines[j].strip()):
                current = stripped
                sections.setdefault(current, [])
                prev_was_dash = False
                i = j + 1
                continue

        prev_was_dash = False
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
    subtitle_bullet_markers = {
        'private repo available on request',
        'private repository available on request',
        'university coursework',
        'university coursework project',
        'university coursework project.',
    }
    for lines in split_entries(block_lines):
        if not lines:
            continue
        title = lines[0]
        subtitle = ''
        # Convert trailing parenthetical text into subtitle, e.g.:
        # "Bunkerify (www.bunkerify.com)" -> title "Bunkerify", subtitle "www.bunkerify.com"
        m = re.match(r'^(.*?)\s*\(([^()]+)\)\s*$', title)
        if m:
            title = m.group(1).strip()
            subtitle = m.group(2).strip()

        date = ''
        idx = 1
        if len(lines) > 1 and DATE_LINE.search(lines[1]):
            date = lines[1]
            idx = 2
        elif len(lines) > 2 and not lines[1].startswith('-') and DATE_LINE.search(lines[2]):
            subtitle = subtitle or lines[1].strip()
            date = lines[2]
            idx = 3
        bullets = []
        for line in lines[idx:]:
            line_s = line.strip()
            if not line_s:
                continue
            if line_s.lower() in ('tasks/achievements', 'courses'):
                continue
            if line.startswith('-'):
                b = line.lstrip('-').strip()
                if b.lower() in subtitle_bullet_markers:
                    marker = b.rstrip('.')
                    if subtitle:
                        if marker.lower() not in subtitle.lower():
                            subtitle = f'{subtitle} - {marker}'
                    else:
                        subtitle = marker
                    continue
                bullets.append(b)
            elif not subtitle and not DATE_LINE.search(line_s):
                subtitle = line_s
        entries.append({
            'title': title,
            'subtitle': subtitle,
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
            part = clean_skill_token(part)
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


def parse_summary_new(block_lines) -> str:
    return ' '.join([l.strip() for l in (block_lines or []) if l.strip()])


def parse_projects_new(block_lines) -> dict[str, dict]:
    projects: dict[str, dict] = {}
    current = None

    def flush_project():
        nonlocal current
        if not current:
            return
        title = current.get('title', '').strip()
        if not title:
            current = None
            return
        subtitle_parts = []
        if current.get('link'):
            subtitle_parts.append(f'LINK: {current["link"]}')
        if current.get('live'):
            subtitle_parts.append(f'LIVE: {current["live"]}')
        projects[title] = {
            'title': title,
            'subtitle': ' | '.join(subtitle_parts),
            'date': '',
            'bullets': current.get('bullets', [])[:],
            'raw': '',
        }
        current = None

    for raw in block_lines or []:
        line = normalize_text(raw).strip()
        if not line:
            continue
        if line.startswith('PROJECT:'):
            flush_project()
            current = {'title': line.split(':', 1)[1].strip(), 'link': '', 'live': '', 'bullets': []}
            continue
        if current is None:
            continue
        if line.startswith('LINK:'):
            current['link'] = line.split(':', 1)[1].strip()
            continue
        if line.startswith('LIVE:'):
            current['live'] = line.split(':', 1)[1].strip()
            continue
        if line.startswith('-'):
            bullet = line.lstrip('-').strip()
            if bullet:
                current['bullets'].append(bullet)
    flush_project()
    return projects


def parse_experience_new(block_lines):
    # New format still uses title/subtitle/date/bullets with blank lines between roles.
    return parse_experience(block_lines or [])


def parse_skills_new(block_lines):
    skills = []
    for raw in block_lines or []:
        line = normalize_text(raw).strip()
        if not line:
            continue
        if line.endswith(':'):
            continue
        if ':' in line and not line.startswith(('http://', 'https://')):
            line = line.split(':', 1)[1].strip()
        for part in _split_skills_csv(line):
            for sub in part.split(','):
                token = clean_skill_token(sub)
                if token:
                    skills.append(token)
    seen = set()
    out = []
    for skill in skills:
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(skill)
    return out


def _get_section_case_insensitive(source_sections: dict, *names: str) -> list[str]:
    lower_map = {normalize_text(k).strip().lower(): v for k, v in (source_sections or {}).items()}
    for name in names:
        value = lower_map.get(normalize_text(name).strip().lower())
        if value is not None:
            return value
    return []


def parse_resume_sections(resume_text: str) -> dict:
    _, source_sections = split_sections(resume_text)
    if _get_section_case_insensitive(source_sections, 'PROFESSIONAL SUMMARY'):
        summary = parse_summary_new(_get_section_case_insensitive(source_sections, 'PROFESSIONAL SUMMARY'))
        projects = parse_projects_new(_get_section_case_insensitive(source_sections, 'PROJECTS'))
        experience = parse_experience_new(_get_section_case_insensitive(source_sections, 'PROFESSIONAL EXPERIENCE'))
        education = parse_education(_get_section_case_insensitive(source_sections, 'EDUCATION'))
        skills = parse_skills_new(_get_section_case_insensitive(source_sections, 'SKILLS'))
        certificates = parse_list(_get_section_case_insensitive(source_sections, 'CERTIFICATES'))
        return {
            'summary': summary,
            'projects': projects,
            'skills': skills,
            'certificates': certificates,
            'experience': experience,
            'education': education,
            'interests': [],
        }

    # Backwards compatibility for old resume format.
    summary_lines = _get_section_case_insensitive(source_sections, 'Software Engineer')
    summary = ' '.join([l for l in summary_lines if l.strip()])

    work_entries = parse_experience(_get_section_case_insensitive(source_sections, 'Work experience/Projects'))
    work_entries = [e for e in work_entries if not _is_excluded_project_title(e.get('title', ''))]
    volunteer_entries = parse_experience(_get_section_case_insensitive(source_sections, 'Volunteer Experience'))

    projects = {}
    experience = []
    for entry in work_entries:
        title_l = normalize_text(entry.get('title', '')).lower()
        if 'independent contractor' in title_l or 'web developer' in title_l or 'driver' in title_l:
            experience.append(dict(entry))
        else:
            projects[entry.get('title', '')] = dict(entry)

    return {
        'summary': summary,
        'projects': projects,
        'skills': parse_skills(_get_section_case_insensitive(source_sections, 'Skills')),
        'certificates': parse_list(_get_section_case_insensitive(source_sections, 'Certificates')),
        'experience': experience + volunteer_entries,
        'education': parse_education(_get_section_case_insensitive(source_sections, 'Education')),
        'interests': parse_list(_get_section_case_insensitive(source_sections, 'Interests')),
    }


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


def _is_excluded_project_title(title: str) -> bool:
    return normalize_text(title).strip().lower() in EXCLUDED_PROJECT_TITLES


def _filter_excluded_entries_in_sections(sections) -> None:
    for section in sections:
        if not section.get('entries'):
            continue
        section['entries'] = [
            e for e in section['entries']
            if not _is_excluded_project_title(e.get('title', ''))
        ]


def _extract_response_text(response) -> str:
    text = getattr(response, 'output_text', None)
    if text:
        return text.strip()
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
        return "\n".join([p for p in parts if p]).strip()
    return ''


def _extract_anthropic_text(response) -> str:
    content = getattr(response, 'content', []) or []
    parts = []
    for block in content:
        b_type = getattr(block, 'type', None) or (block.get('type') if isinstance(block, dict) else None)
        if b_type != 'text':
            continue
        text = getattr(block, 'text', None) or (block.get('text') if isinstance(block, dict) else None)
        if text:
            parts.append(text)
    return '\n'.join(parts).strip()


def _get_ai_provider() -> str | None:
    preferred = (os.environ.get('AI_PROVIDER', '') or '').strip().lower()
    if preferred == 'openai' and os.environ.get('OPENAI_API_KEY'):
        return 'openai'
    if preferred == 'anthropic' and os.environ.get('ANTHROPIC_API_KEY'):
        return 'anthropic'
    if os.environ.get('OPENAI_API_KEY'):
        return 'openai'
    if os.environ.get('ANTHROPIC_API_KEY'):
        return 'anthropic'
    return None


def _call_claude(
    prompt: str,
    instructions: str | None = None,
    max_tokens: int = 2400,
    model_tier: str = 'smart',
) -> str:
    try:
        from anthropic import Anthropic
    except Exception as e:
        raise SystemExit('Anthropic SDK required. Install with: python -m pip install anthropic') from e
    tier = (model_tier or 'smart').strip().lower()
    if tier == 'fast':
        model = os.environ.get('CLAUDE_MODEL_FAST', 'claude-haiku-4-5-20251001')
    else:
        model = os.environ.get('CLAUDE_MODEL_SMART', 'claude-sonnet-4-20250514')
    # Backward compatibility for older env wiring.
    model = (model or '').strip() or os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-4-20250514')
    client = Anthropic()
    kwargs = {
        'model': model,
        'max_tokens': max(64, int(max_tokens)),
        'messages': [{'role': 'user', 'content': prompt}],
    }
    if instructions:
        kwargs['system'] = instructions
    response = client.messages.create(**kwargs)
    text = _extract_anthropic_text(response)
    if text:
        return text
    raise SystemExit('Anthropic response did not include text output.')


def _call_ai_text(
    prompt: str,
    instructions: str | None = None,
    max_tokens: int = 2400,
    model_tier: str = 'smart',
) -> str:
    provider = _get_ai_provider()
    if provider == 'openai':
        try:
            from openai import OpenAI
        except Exception as e:
            raise SystemExit('OpenAI SDK required. Install with: python -m pip install openai') from e
        tier = (model_tier or 'smart').strip().lower()
        if tier == 'fast':
            model = os.environ.get('OPENAI_MODEL_FAST', os.environ.get('OPENAI_MODEL', 'gpt-4o-mini'))
        else:
            model = os.environ.get('OPENAI_MODEL_SMART', os.environ.get('OPENAI_MODEL', 'gpt-4o'))
        client = OpenAI()
        kwargs = {'model': model, 'input': prompt}
        if instructions:
            kwargs['instructions'] = instructions
        response = client.responses.create(**kwargs)
        text = _extract_response_text(response)
        if text:
            return text
        raise SystemExit('OpenAI response did not include text output.')

    if provider == 'anthropic':
        return _call_claude(
            prompt=prompt,
            instructions=instructions,
            max_tokens=max_tokens,
            model_tier=model_tier,
        )

    raise SystemExit('Set OPENAI_API_KEY or ANTHROPIC_API_KEY to enable AI features.')


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ''
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def assess_job_fit_with_ai(job_text: str, resume_text: str) -> dict:
    job_text = _truncate(job_text, 2000)
    resume_text = _truncate(resume_text, 3000)
    prompt = (
        "Assess whether the candidate should apply for this role based only on the resume.\n"
        "Return strict JSON only with keys: recommendation, confidence, rationale, matched_requirements, missing_requirements.\n"
        "Rules:\n"
        "- recommendation: one of APPLY, MAYBE, NO\n"
        "- confidence: integer 0-100\n"
        "- rationale: one short sentence\n"
        "- matched_requirements: array of concise requirement statements found in both job description and resume\n"
        "- missing_requirements: array of concise requirement statements present in job description but not evidenced in resume\n"
        "- keep each array item short and specific\n"
        "- Do not invent resume facts.\n\n"
        f"Job description:\n{job_text}\n\n"
        f"Resume:\n{resume_text}\n"
    )
    raw = _call_ai_text(prompt=prompt, max_tokens=512, model_tier='fast')
    if not raw:
        raise SystemExit('AI response did not include text output.')

    # Model sometimes wraps JSON with prose; extract first JSON object.
    match = re.search(r'\{[\s\S]*\}', raw)
    payload = match.group(0) if match else raw
    data = json.loads(payload)

    rec = str(data.get('recommendation', 'MAYBE')).upper().strip()
    if rec not in ('APPLY', 'MAYBE', 'NO'):
        rec = 'MAYBE'
    try:
        confidence = int(data.get('confidence', 50))
    except Exception:
        confidence = 50
    confidence = max(0, min(100, confidence))
    rationale = str(data.get('rationale', '')).strip()
    matched = data.get('matched_requirements', [])
    if not isinstance(matched, list):
        matched = []
    matched = [str(m).strip() for m in matched if str(m).strip()][:15]
    missing = data.get('missing_requirements', [])
    if not isinstance(missing, list):
        missing = []
    missing = [str(m).strip() for m in missing if str(m).strip()][:15]
    return {
        'recommendation': rec,
        'confidence': confidence,
        'rationale': rationale,
        'matched_requirements': matched,
        'missing_requirements': missing,
        'gaps': missing[:3],
    }


def assess_job_fit_with_claude(job_text: str, resume_text: str) -> dict:
    return assess_job_fit_with_ai(job_text=job_text, resume_text=resume_text)


def assess_job_fit(job_text: str, resume_text: str) -> dict:
    if not job_text.strip():
        return {
            'recommendation': 'MAYBE',
            'confidence': 0,
            'rationale': 'Paste a job description to get an apply recommendation.',
            'matched_requirements': [],
            'missing_requirements': [],
            'gaps': [],
        }

    if _get_ai_provider():
        try:
            cache_key = hashlib.sha256((job_text + resume_text).encode('utf-8', errors='replace')).hexdigest()
            cached = _JOB_FIT_CACHE.get(cache_key)
            if cached is not None:
                return dict(cached)
            fit = assess_job_fit_with_claude(job_text=job_text, resume_text=resume_text)
            _JOB_FIT_CACHE[cache_key] = dict(fit)
            return fit
        except Exception:
            pass

    # Heuristic fallback when AI provider is unavailable or fails.
    resume_norm = normalize_text(resume_text).lower()
    words = [
        w for w in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', normalize_text(job_text).lower())
        if len(w) >= 3 and w not in STOPWORDS
    ]
    if not words:
        return {
            'recommendation': 'MAYBE',
            'confidence': 40,
            'rationale': 'Not enough detail in the job description to score fit accurately.',
            'matched_requirements': [],
            'missing_requirements': [],
            'gaps': [],
        }
    top_words = []
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    for w, _ in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:18]:
        top_words.append(w)
    matched = [w for w in top_words if w in resume_norm]
    ratio = len(matched) / max(1, len(top_words))
    confidence = int(ratio * 100)
    if confidence >= 65:
        rec = 'APPLY'
    elif confidence >= 40:
        rec = 'MAYBE'
    else:
        rec = 'NO'
    missing = [w for w in top_words if w not in resume_norm]
    matched_requirements = [w for w in top_words if w in resume_norm]
    return {
        'recommendation': rec,
        'confidence': confidence,
        'rationale': f'Match score based on keyword overlap: {confidence}%.',
        'matched_requirements': matched_requirements,
        'missing_requirements': missing,
        'gaps': missing[:3],
    }


def _clean_top_keywords(values, limit: int = 10) -> list[str]:
    out = []
    seen = set()
    for raw in values or []:
        text = normalize_text(str(raw)).strip().lower()
        text = re.sub(r'[^a-z0-9\+\#\-\s]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            continue
        if text in STOPWORDS:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _sanitize_job_classification(raw: dict | None) -> dict:
    data = raw if isinstance(raw, dict) else {}
    primary = str(data.get('primary_category', 'unknown')).strip().lower()
    if primary not in VALID_PRIMARY_CATEGORIES:
        primary = 'unknown'
    tone = str(data.get('tone', 'unknown')).strip().lower()
    if tone not in VALID_TONES:
        tone = 'unknown'
    try:
        confidence = int(data.get('confidence', 0))
    except Exception:
        confidence = 0
    confidence = max(0, min(100, confidence))
    keywords = _clean_top_keywords(data.get('top_keywords', []), limit=10)
    return {
        'primary_category': primary,
        'top_keywords': keywords,
        'tone': tone,
        'confidence': confidence,
    }


def classify_job_with_ai(job_text: str) -> dict:
    payload = _truncate(job_text or '', 4000)
    instructions = (
        "Classify the job description and return STRICT JSON only.\n"
        "Do not make assumptions beyond the provided text.\n"
        "Do not infer candidate experience or add facts.\n"
        "Output keys exactly:\n"
        "{"
        "\"primary_category\": \"software_engineering\"|\"cybersecurity\"|\"frontend\"|\"backend\"|\"devops\"|\"unknown\","
        "\"top_keywords\": [string],"
        "\"tone\": \"corporate\"|\"startup\"|\"security-heavy\"|\"unknown\","
        "\"confidence\": integer 0-100"
        "}\n"
        "Rules:\n"
        "- top_keywords must contain up to 10 concise keywords from the job text.\n"
        "- If uncertain, choose unknown with lower confidence.\n"
        "- No prose, markdown, or extra keys."
    )
    text = _call_ai_text(
        prompt=f"Job description:\n{payload}\n",
        instructions=instructions,
        max_tokens=420,
        model_tier='fast',
    )
    match = re.search(r'\{[\s\S]*\}', text or '')
    raw = match.group(0) if match else (text or '{}')
    data = json.loads(raw)
    return _sanitize_job_classification(data)


def classify_job_heuristic(job_text: str) -> dict:
    text = normalize_text(job_text or '')
    job_norm = text.lower()
    tokens = [
        w for w in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', job_norm)
        if len(w) >= 3 and w not in STOPWORDS
    ]
    freq = {}
    for token in tokens:
        freq[token] = freq.get(token, 0) + 1

    multi_terms = {
        'cybersecurity': CYBER_JOB_TERMS + [
            'incident response', 'threat hunting', 'security operations', 'essential eight',
            'appsec', 'application security', 'security engineer', 'devsecops', 'sast', 'dast',
            'penetration testing', 'vulnerability management',
        ],
        'frontend': [
            'frontend', 'front-end', 'react', 'typescript', 'javascript', 'html', 'css',
            'next.js', 'nextjs', 'ui', 'ux',
        ],
        'backend': [
            'backend', 'back-end', 'api', 'rest', 'microservices', 'node', 'python', 'java',
            'sql', 'postgres', 'database',
        ],
        'devops': [
            'devops', 'sre', 'ci/cd', 'ci cd', 'kubernetes', 'docker', 'terraform', 'aws',
            'azure', 'gcp', 'monitoring',
        ],
        'software_engineering': [
            'software engineer', 'software engineering', 'full stack', 'full-stack',
            'application development', 'agile', 'developer', 'api',
        ],
    }
    token_terms = {
        'cybersecurity': ['cyber', 'security', 'soc', 'siem', 'incident', 'threat', 'vulnerability'],
        'frontend': ['frontend', 'react', 'typescript', 'javascript', 'html', 'css', 'ui', 'ux'],
        'backend': ['backend', 'api', 'rest', 'microservices', 'node', 'python', 'java', 'sql'],
        'devops': ['devops', 'sre', 'kubernetes', 'docker', 'terraform', 'aws', 'azure', 'gcp'],
        'software_engineering': ['engineer', 'software', 'development', 'coding'],
    }

    scores = {k: 0 for k in token_terms}
    for category, terms in multi_terms.items():
        for term in terms:
            if term in job_norm:
                scores[category] += 3
    for category, terms in token_terms.items():
        for term in terms:
            scores[category] += freq.get(term, 0)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_cat, best_score = ranked[0]
    second_cat, second_score = ranked[1]
    primary = 'unknown'
    if best_score > 0:
        primary = best_cat
    if (
        primary == 'software_engineering'
        and scores.get('cybersecurity', 0) >= best_score
        and _security_analyst_dominance(job_text)
    ):
        primary = 'cybersecurity'

    tone = 'unknown'
    if any(term in job_norm for term in ('startup', 'fast-paced', 'fast paced', 'scale-up', 'greenfield')):
        tone = 'startup'
    if any(term in job_norm for term in ('bank', 'enterprise', 'compliance', 'stakeholder', 'governance')):
        tone = 'corporate'
    if scores.get('cybersecurity', 0) >= 6 or any(
        term in job_norm for term in ('essential eight', 'siem', 'soc', 'incident response')
    ):
        tone = 'security-heavy'

    top_keywords = [w for w, _ in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:10]]
    confidence = 0
    if best_score > 0:
        margin = best_score - second_score
        confidence = min(95, 45 + best_score * 4 + max(0, margin * 5))
    elif tokens:
        confidence = 20

    return _sanitize_job_classification({
        'primary_category': primary,
        'top_keywords': top_keywords,
        'tone': tone,
        'confidence': confidence,
    })


def _security_analyst_dominance(job_text: str) -> bool:
    norm = _normalize_term(job_text or '')
    strong_markers = [
        'security analyst',
        'soc analyst',
        'siem',
        'threat hunting',
        'incident response',
        'vulnerability assessment',
        'penetration testing',
        'nmap',
        'metasploit',
        'wireshark',
        'burp suite',
    ]
    hits = sum(1 for term in strong_markers if term in norm)
    return hits >= 2


def _apply_classification_guardrails(job_text: str, result: dict) -> dict:
    safe = _sanitize_job_classification(result or {})
    norm = _normalize_term(job_text or '')
    has_appsec_signal = any(term in norm for term in APPSEC_DEVSECOPS_ROLE_TERMS)
    has_swe_signal = any(term in norm for term in SOFTWARE_FULLSTACK_ROLE_TERMS)
    software_signals = [
        'software engineer',
        'graduate software engineer',
        'full stack',
        'typescript',
        'react',
        'java',
        'sql',
    ]
    has_software_signal = any(term in norm for term in software_signals)
    has_explicit_swe_title = 'software engineer' in norm or 'graduate software engineer' in norm
    if has_appsec_signal:
        safe['primary_category'] = 'cybersecurity'
        safe['confidence'] = max(70, int(safe.get('confidence', 70)))
    if has_explicit_swe_title and safe.get('primary_category') in {'backend', 'frontend', 'devops', 'unknown'}:
        safe['primary_category'] = 'software_engineering'
        safe['confidence'] = max(60, int(safe.get('confidence', 60)))
    if has_swe_signal and safe.get('primary_category') in {'frontend', 'backend', 'devops', 'unknown'}:
        safe['primary_category'] = 'software_engineering'
        safe['confidence'] = max(60, int(safe.get('confidence', 60)))
    if (
        safe.get('primary_category') == 'cybersecurity'
        and has_software_signal
        and not has_appsec_signal
        and not _security_analyst_dominance(job_text)
    ):
        safe['primary_category'] = 'software_engineering'
        safe['confidence'] = max(50, int(safe.get('confidence', 50)) - 10)
    return safe


def classify_job(job_text: str) -> dict:
    key = hashlib.sha256(normalize_text(job_text or '').encode('utf-8', errors='replace')).hexdigest()
    cached = _JOB_CLASSIFICATION_CACHE.get(key)
    if cached is not None:
        return dict(cached)

    result = None
    if (job_text or '').strip() and _get_ai_provider():
        try:
            result = classify_job_with_ai(job_text=job_text)
        except Exception:
            result = None
    if result is None:
        result = classify_job_heuristic(job_text=job_text)
    guarded = _apply_classification_guardrails(job_text=job_text, result=result)
    _JOB_CLASSIFICATION_CACHE[key] = dict(guarded)
    return guarded


def _has_explicit_mobile_requirement(job_text: str) -> bool:
    norm = _normalize_term(job_text or '')
    return any(_normalize_term(term) in norm for term in EXPLICIT_MOBILE_REQUIREMENT_TERMS)


def _detect_role_profile(job_text: str, classification: dict | None = None) -> str:
    norm = _normalize_term(job_text or '')
    category = _normalize_term(str((classification or {}).get('primary_category', 'unknown')))
    has_explicit_mobile = _has_explicit_mobile_requirement(job_text)

    if any(_normalize_term(term) in norm for term in PENTEST_ROLE_TERMS):
        return 'pentest'
    if any(_normalize_term(term) in norm for term in APPSEC_DEVSECOPS_ROLE_TERMS):
        return 'appsec_devsecops'
    if has_explicit_mobile and any(_normalize_term(term) in norm for term in MOBILE_ROLE_TERMS):
        return 'mobile'
    if any(_normalize_term(term) in norm for term in JUNIOR_GRADUATE_ROLE_TERMS):
        return 'graduate'
    if category == 'cybersecurity':
        return 'appsec_devsecops'
    if category in {'software_engineering', 'frontend', 'backend', 'devops'}:
        return 'software_engineering'
    return 'general'


def _extract_primary_degree(resume_json: dict) -> str:
    for edu in resume_json.get('education', []) or []:
        degree = normalize_text(str(edu.get('degree', ''))).strip()
        if degree:
            return degree
    return 'Computer Science degree'


def build_summary(classification: dict, resume_json: dict, job_text: str = '') -> str:
    profile = _detect_role_profile(job_text=job_text, classification=classification)
    degree = _extract_primary_degree(resume_json)

    who_i_am = 'Software Engineer'
    if profile == 'appsec_devsecops':
        who_i_am = 'Application Security-focused Software Engineer'
    elif profile == 'pentest':
        who_i_am = 'Offensive Security-focused Software Engineer'
    elif profile == 'mobile':
        who_i_am = 'Mobile-focused Software Engineer'
    elif profile == 'graduate':
        who_i_am = 'Graduate Software Engineer'

    article = 'an' if who_i_am[:1].lower() in {'a', 'e', 'i', 'o', 'u'} else 'a'
    sentence_one = f"I am {article} {who_i_am} with a {degree}."
    if profile == 'appsec_devsecops':
        sentence_two = (
            "I focus on security tooling across CI/CD, including SAST/DAST workflows, "
            "vulnerability remediation, and production hardening."
        )
    elif profile == 'pentest':
        sentence_two = (
            "I focus on offensive security workflows, vulnerability assessment, "
            "penetration testing, and practical remediation outcomes."
        )
    elif profile in {'software_engineering', 'graduate'}:
        sentence_two = (
            "I build full-stack systems with AI-assisted features across React frontends, "
            "Python/API backends, and reliable deployment pipelines."
        )
    elif profile == 'mobile':
        sentence_two = (
            "I build mobile-first product features with React Native while integrating secure APIs "
            "and production deployment workflows."
        )
    else:
        sentence_two = (
            "I build production-ready software with strong full-stack fundamentals, "
            "secure engineering practices, and measurable delivery outcomes."
        )
    return f"{sentence_one} {sentence_two}"


def choose_resume_strategy(classification: dict, job_text: str = '') -> dict:
    profile = _detect_role_profile(job_text=job_text, classification=classification)
    if profile == 'appsec_devsecops':
        return {
            'name': 'APPSEC_DEVSECOPS',
            'tagline': None,
            'section_order': [
                'Professional Summary',
                'Key Skills / Technical Skills',
                'Projects',
                'Professional Experience',
                'Education',
                'Certifications',
            ],
            'project_priority': [
                'Production Support Incident Console',
                'Job Application Assistant',
                'Bunkerify',
            ],
            'skill_priority_groups': ['security', 'backend', 'testing', 'frontend', 'languages', 'tools'],
            'min_bullets_per_project': 2,
            'max_bullets_per_project': 4,
            'max_bullets_per_experience': 3,
            'max_skills': 14,
            'min_skills': 10,
            'prefer_cyber_terms': True,
            'target_max_pages': 1.5,
            'role_profile': profile,
        }

    if profile == 'pentest':
        return {
            'name': 'PENTEST',
            'tagline': None,
            'section_order': [
                'Professional Summary',
                'Key Skills / Technical Skills',
                'Projects',
                'Professional Experience',
                'Education',
                'Certifications',
            ],
            'project_priority': [
                'Bunkerify',
                'Production Support Incident Console',
                'Job Application Assistant',
            ],
            'skill_priority_groups': ['security', 'backend', 'testing', 'frontend', 'languages', 'tools'],
            'min_bullets_per_project': 2,
            'max_bullets_per_project': 4,
            'max_bullets_per_experience': 3,
            'max_skills': 14,
            'min_skills': 10,
            'prefer_cyber_terms': True,
            'target_max_pages': 1.5,
            'role_profile': profile,
        }

    if profile in {'software_engineering', 'graduate'}:
        return {
            'name': 'SOFTWARE_GENERAL' if profile == 'software_engineering' else 'GRADUATE',
            'tagline': None,
            'section_order': DEFAULT_SECTION_ORDER[:],
            'project_priority': [
                'Job Application Assistant',
                'Production Support Incident Console',
                'Bunkerify',
            ],
            'skill_priority_groups': ['frontend', 'backend', 'languages', 'testing', 'security', 'tools'],
            'min_bullets_per_project': 2,
            'max_bullets_per_project': 4,
            'max_bullets_per_experience': 3,
            'max_skills': 10,
            'min_skills': 8,
            'prefer_cyber_terms': False,
            'target_max_pages': 1.0,
            'role_profile': profile,
        }

    if profile == 'mobile':
        return {
            'name': 'MOBILE',
            'tagline': None,
            'section_order': DEFAULT_SECTION_ORDER[:],
            'project_priority': [
                'Cancer Awareness Mobile App',
                'Job Application Assistant',
                'Production Support Incident Console',
                'Bunkerify',
            ],
            'skill_priority_groups': ['frontend', 'backend', 'languages', 'testing', 'security', 'tools'],
            'min_bullets_per_project': 2,
            'max_bullets_per_project': 4,
            'max_bullets_per_experience': 3,
            'max_skills': 10,
            'min_skills': 8,
            'prefer_cyber_terms': False,
            'target_max_pages': 1.0,
            'role_profile': profile,
        }

    # Default to general ordering for unknown roles.
    return {
        'name': 'GENERAL',
        'tagline': None,
        'section_order': DEFAULT_SECTION_ORDER[:],
        'project_priority': [
            'Job Application Assistant',
            'Production Support Incident Console',
            'Bunkerify',
        ],
        'skill_priority_groups': ['frontend', 'backend', 'languages', 'testing', 'security', 'tools'],
        'min_bullets_per_project': 2,
        'max_bullets_per_project': 4,
        'max_bullets_per_experience': 3,
        'max_skills': 10,
        'min_skills': 8,
        'prefer_cyber_terms': False,
        'target_max_pages': 1.0,
        'role_profile': profile,
    }


def reorder_projects_by_priority(projects: list[dict], priority: list[str]) -> list[dict]:
    if not projects:
        return []
    priority_l = [normalize_text(p).lower() for p in (priority or []) if normalize_text(p).strip()]

    def project_key(entry: dict):
        title = normalize_text(entry.get('title', '')).lower()
        for idx, marker in enumerate(priority_l):
            if marker in title:
                return (0, idx, title)
        return (1, 99, title)

    return sorted(projects, key=project_key)


def filter_projects_for_role(projects: list[dict], role_profile: str, job_text: str = '') -> list[dict]:
    out = []
    include_cancer = role_profile == 'mobile' or (
        role_profile == 'graduate' and _has_explicit_mobile_requirement(job_text)
    )
    for project in projects or []:
        title = _normalize_term(project.get('title', ''))
        if role_profile == 'pentest' and 'job application assistant' in title:
            continue
        if 'cancer awareness mobile app' in title and not include_cancer:
            continue
        out.append(project)
    return out


def select_experience_bullets_for_render(
    entries: list[dict],
    min_bullets: int = 2,
    max_bullets: int = 2,
) -> list[dict]:
    min_keep = max(1, int(min_bullets or 2))
    max_keep = max(min_keep, int(max_bullets or 2))
    selected = []
    for entry in entries or []:
        updated = dict(entry)
        bullet_objects = normalize_bullet_list(updated.get('bullet_objects', updated.get('bullets', [])))
        ranked = []
        for idx, bullet_obj in enumerate(bullet_objects):
            try:
                importance = int(bullet_obj.get('importance', 0))
            except Exception:
                importance = 0
            importance = max(0, min(3, importance))
            ranked.append((importance, idx, bullet_obj))

        ranked.sort(key=lambda t: (-t[0], t[1]))
        chosen = []
        for _, _, bullet_obj in ranked:
            if len(chosen) >= max_keep:
                break
            chosen.append(bullet_obj)

        if len(chosen) < min_keep:
            for bullet_obj in bullet_objects:
                if bullet_obj in chosen:
                    continue
                chosen.append(bullet_obj)
                if len(chosen) >= min_keep or len(chosen) >= max_keep:
                    break

        updated['bullet_objects'] = chosen
        updated['bullets'] = [b.get('text', '') for b in chosen if b.get('text', '')]
        selected.append(updated)
    return selected


def apply_ai_project_selection_and_order(
    projects: list[dict],
    selected_projects,
    project_order,
) -> list[dict]:
    if not projects:
        return []
    by_key = {_entry_match_key(p.get('title', '')): p for p in projects}
    keys = list(by_key.keys())

    selected_keys = []
    if isinstance(selected_projects, list) and selected_projects:
        for title in selected_projects:
            key = _entry_match_key(str(title))
            if key in by_key and key not in selected_keys:
                selected_keys.append(key)
        if not selected_keys:
            selected_keys = keys[:]
    else:
        selected_keys = keys[:]

    ordered = []
    if isinstance(project_order, list) and project_order:
        for title in project_order:
            key = _entry_match_key(str(title))
            if key in selected_keys and key not in ordered:
                ordered.append(key)
    for key in selected_keys:
        if key not in ordered:
            ordered.append(key)
    return [by_key[k] for k in ordered if k in by_key]


def _keyword_overlap_score(text: str, keywords: list[str]) -> int:
    norm = _normalize_term(text)
    score = 0
    for kw in keywords:
        k = _normalize_term(kw)
        if k and k in norm:
            score += 1
    return score


def reorder_skill_groups(grouped_skills: dict, priority_groups: list[str]) -> tuple[dict, list[str]]:
    grouped = grouped_skills or {}
    ordered_group_names = []
    for group in priority_groups or []:
        if group in grouped and group not in ordered_group_names:
            ordered_group_names.append(group)
    for group in grouped.keys():
        if group not in ordered_group_names:
            ordered_group_names.append(group)
    ordered_grouped = {k: grouped.get(k, []) for k in ordered_group_names}
    flattened = []
    seen = set()
    for group in ordered_group_names:
        for skill in ordered_grouped.get(group, []):
            cleaned = clean_skill_token(str(skill))
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            flattened.append(cleaned)
    return ordered_grouped, flattened


def _normalize_skill_match_text(value: str) -> str:
    text = normalize_text(value or '').lower()
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _all_words_within_window(skill_norm: str, jd_tokens: list[str], window_size: int = 6) -> bool:
    words = [w for w in skill_norm.split(' ') if w]
    if not words or not jd_tokens:
        return False
    if len(words) == 1:
        return words[0] in set(jd_tokens)

    target = set(words)
    span = max(window_size, len(words))
    for start in range(len(jd_tokens)):
        end = min(len(jd_tokens), start + span)
        if target.issubset(set(jd_tokens[start:end])):
            return True
    return False


def _is_software_role_category(role_category: str) -> bool:
    cat = _normalize_term(role_category or '')
    return cat in {'software_engineering', 'frontend', 'backend', 'devops', 'unknown'}


def _infer_bullet_selection_profile(job_text: str, role_category: str) -> dict:
    jd_norm = _normalize_term(job_text or '')
    has_appsec_signal = any(_normalize_term(term) in jd_norm for term in APPSEC_DEVSECOPS_ROLE_TERMS)
    has_swe_signal = any(_normalize_term(term) in jd_norm for term in SOFTWARE_FULLSTACK_ROLE_TERMS)
    has_junior_signal = any(_normalize_term(term) in jd_norm for term in JUNIOR_GRADUATE_ROLE_TERMS)

    role_norm = _normalize_term(role_category or '')
    if has_appsec_signal:
        focus = 'appsec_devsecops'
    elif has_swe_signal or role_norm in {'software_engineering', 'frontend', 'backend', 'devops'}:
        focus = 'software_fullstack'
    else:
        focus = 'general'

    return {
        'focus': focus,
        'junior_or_graduate': has_junior_signal,
    }


def _bullet_relevance_signals(bullet_obj: dict, jd_norm: str, jd_words: set[str], jd_tech_terms: set[str]) -> tuple[bool, bool]:
    text_norm = _normalize_term(bullet_obj.get('text', ''))
    tags = {_normalize_term(tag) for tag in (bullet_obj.get('tags', []) or []) if _normalize_term(tag)}

    relevant = False
    explicit = False

    for tag in tags:
        if not tag:
            continue
        tag_tokens = [t for t in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', tag) if t]
        if tag in jd_norm or any(tok in jd_words for tok in tag_tokens):
            relevant = True
            explicit = True

    text_tokens = [
        tok for tok in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', text_norm)
        if len(tok) >= 3 and tok not in STOPWORDS
    ]
    token_hits = sum(1 for tok in text_tokens if tok in jd_words)
    if token_hits >= 1:
        relevant = True
    if token_hits >= 2:
        explicit = True

    bullet_tech_terms = _extract_tech_terms(text_norm)
    if bullet_tech_terms & jd_tech_terms:
        relevant = True
        explicit = True

    return relevant, explicit


def select_skills_deterministic(
    job_text: str,
    grouped_skills: dict,
    max_skills: int = 18,
    role_category: str = 'software_engineering',
) -> list[str]:
    _ordered_grouped, ordered_skills = reorder_skill_groups(grouped_skills=grouped_skills or {}, priority_groups=SKILL_GROUP_KEYS)
    if not ordered_skills:
        print('[skills-selector] selected 0 skills: []')
        return []

    role_norm = _normalize_term(role_category or '')
    max_skills = max(1, int(max_skills or 18))
    if role_norm == 'software_engineering':
        max_skills = min(max_skills, 18)
    jd_norm = _normalize_skill_match_text(job_text or '')
    jd_tokens = jd_norm.split(' ') if jd_norm else []
    jd_token_set = set(jd_tokens)
    mobile_terms = ('mobile', 'ios', 'android', 'react native')
    quality_terms = (
        'clean code', 'code review', 'code reviews', 'quality', 'ticket', 'tickets', 'bug', 'bugs',
        'support', 'production support', 'incident support',
    )
    styling_terms = (
        'frontend styling', 'styling', 'design system', 'component styling', 'ui styling',
        'css', 'tailwind', 'sass', 'responsive design',
    )
    mentions_frontend_styling = any(t in jd_norm for t in styling_terms)
    prefers_testing = any(t in jd_norm for t in quality_terms)
    style_build_tools = {'tailwind css', 'vite', 'next js', 'nextjs', 'framer motion'}

    def score_skill(skill: str) -> int:
        s_norm = _normalize_skill_match_text(skill)
        score = 0
        if s_norm and s_norm in jd_norm:
            score += 6
        if s_norm and _all_words_within_window(s_norm, jd_tokens):
            score += 4
        for canonical_skill, synonyms in SKILL_SYNONYM_MAP.items():
            if _normalize_skill_match_text(canonical_skill) != s_norm:
                continue
            for synonym in synonyms:
                synonym_norm = _normalize_skill_match_text(synonym)
                if not synonym_norm:
                    continue
                synonym_words = [w for w in synonym_norm.split(' ') if w]
                if (
                    synonym_norm in jd_norm
                    or _all_words_within_window(synonym_norm, jd_tokens)
                    or (synonym_words and all(w in jd_token_set for w in synonym_words))
                ):
                    score += 3
                    break
            break
        if prefers_testing and s_norm in {'jest', 'react testing library'}:
            score += 3
        if not mentions_frontend_styling and s_norm in style_build_tools:
            score -= 3
        return score

    scored = []
    for idx, skill in enumerate(ordered_skills):
        scored.append((score_skill(skill), idx, skill))
    scored.sort(key=lambda t: (-t[0], t[1]))
    score_by_key = {_normalize_skill_match_text(skill): score for score, _, skill in scored}

    selected = []
    selected_keys = set()

    if role_norm == 'software_engineering':
        for must_have in SOFTWARE_BASELINE_SKILLS:
            for candidate in ordered_skills:
                if _normalize_skill_match_text(candidate) == _normalize_skill_match_text(must_have):
                    key = _normalize_skill_match_text(candidate)
                    if key and key not in selected_keys and len(selected) < max_skills:
                        selected.append(candidate)
                        selected_keys.add(key)
                    break

    if any(t in jd_norm for t in mobile_terms):
        for candidate in ordered_skills:
            if _normalize_skill_match_text(candidate) == 'react native':
                key = _normalize_skill_match_text(candidate)
                if key not in selected_keys and len(selected) < max_skills:
                    selected.append(candidate)
                    selected_keys.add(key)
                break

    for score, _, skill in scored:
        if score <= 0:
            continue
        key = _normalize_skill_match_text(skill)
        if key in selected_keys:
            continue
        selected.append(skill)
        selected_keys.add(key)
        if len(selected) >= max_skills:
            break

    for score, _, skill in scored:
        if len(selected) >= max_skills:
            break
        key = _normalize_skill_match_text(skill)
        if key in selected_keys:
            continue
        selected.append(skill)
        selected_keys.add(key)
    for skill in ordered_skills:
        if len(selected) >= max_skills:
            break
        key = _normalize_skill_match_text(skill)
        if key in selected_keys:
            continue
        selected.append(skill)
        selected_keys.add(key)

    if role_norm == 'software_engineering' and selected:
        must_keys = {_normalize_skill_match_text(s) for s in SOFTWARE_BASELINE_SKILLS}
        removable = []
        for i, skill in enumerate(selected):
            key = _normalize_skill_match_text(skill)
            if key in must_keys:
                continue
            removable.append((score_by_key.get(key, 0), i, skill))
        drop_count = int(len(selected) * 0.25)
        if drop_count > 0 and removable:
            removable.sort(key=lambda t: (t[0], -t[1]))
            drop_set = {skill for _, _, skill in removable[:drop_count]}
            selected = [s for s in selected if s not in drop_set]

        # Re-assert must-include skills if present in inventory.
        selected_keys = {_normalize_skill_match_text(s) for s in selected}
        missing_baseline = []
        for must_have in SOFTWARE_BASELINE_SKILLS:
            for candidate in ordered_skills:
                if _normalize_skill_match_text(candidate) == _normalize_skill_match_text(must_have):
                    key = _normalize_skill_match_text(candidate)
                    if key and key not in selected_keys:
                        missing_baseline.append(candidate)
                        selected_keys.add(key)
                    break
        if missing_baseline:
            selected = missing_baseline + selected

    final_selected = selected[:max_skills]
    print(f"[skills-selector] selected {len(final_selected)} skills: {final_selected}")
    return final_selected


def select_project_bullets_deterministic(
    projects: list[dict],
    job_text: str,
    max_bullets_per_project: int,
    min_bullets_per_project: int = 2,
    role_category: str = 'unknown',
) -> list[dict]:
    max_bullets = max(1, int(max_bullets_per_project or 3))
    min_bullets = max(1, int(min_bullets_per_project or 1))
    jd_norm = _normalize_term(job_text or '')
    jd_words = set(re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', jd_norm))
    jd_tech_terms = _extract_tech_terms(jd_norm)
    profile = _infer_bullet_selection_profile(job_text=job_text, role_category=role_category)
    profile_focus = profile.get('focus', 'general')

    if profile_focus == 'appsec_devsecops':
        priority_tags = APPSEC_PRIORITY_TAGS
        depriority_tags = APPSEC_DEPRIORITY_TAGS
    elif profile_focus == 'software_fullstack':
        priority_tags = SWE_PRIORITY_TAGS
        depriority_tags = set()
    else:
        priority_tags = set()
        depriority_tags = set()

    def score_bullet(bullet_obj: dict) -> int:
        text = bullet_obj.get('text', '')
        text_norm = _normalize_term(text)
        tags = {_normalize_term(tag) for tag in (bullet_obj.get('tags', []) or []) if _normalize_term(tag)}

        tiered_keyword_score = 0
        for term, weight in KEYWORD_TIER_WEIGHTS.items():
            term_n = _normalize_term(term)
            term_match_in_jd = term_n in jd_norm or term_n in jd_words
            if term_n and term_match_in_jd and term_n in text_norm:
                tiered_keyword_score += int(weight)

        tag_matches = 0
        for t in tags:
            if t and (t in jd_norm or t in jd_words):
                tag_matches += 1
        tag_score = min(6, tag_matches * 2)

        relevance, _explicit = _bullet_relevance_signals(
            bullet_obj=bullet_obj,
            jd_norm=jd_norm,
            jd_words=jd_words,
            jd_tech_terms=jd_tech_terms,
        )

        priority_score = 0
        if tags & priority_tags:
            priority_score += 6
        if tags & depriority_tags:
            priority_score -= 6
        if relevance:
            priority_score += 2

        importance_score = max(0, min(3, int(bullet_obj.get('importance', 0)))) * 3
        core_score = 2 if bullet_obj.get('core') else 0
        return tiered_keyword_score + tag_score + importance_score + priority_score + core_score

    selected = []

    for project in projects or []:
        bullet_objects = normalize_bullet_list(project.get('bullet_objects', project.get('bullets', [])))
        if not bullet_objects:
            updated = dict(project)
            updated['bullets'] = []
            updated['bullet_objects'] = []
            selected.append(updated)
            continue

        eligible_rows = []
        for idx, bullet_obj in enumerate(bullet_objects):
            try:
                importance = int(bullet_obj.get('importance', 0))
            except Exception:
                importance = 0
            importance = max(0, min(3, importance))

            if importance == 1:
                continue
            _relevant, explicit = _bullet_relevance_signals(
                bullet_obj=bullet_obj,
                jd_norm=jd_norm,
                jd_words=jd_words,
                jd_tech_terms=jd_tech_terms,
            )
            if not (importance == 3 or (importance == 2 and explicit)):
                continue

            eligible_rows.append({
                'idx': idx,
                'bullet': bullet_obj,
                'score': score_bullet(bullet_obj),
                'importance': importance,
            })

        imp3_rows = [r for r in eligible_rows if int(r.get('importance', 0)) == 3]
        imp2_rows = [r for r in eligible_rows if int(r.get('importance', 0)) == 2]
        imp3_rows.sort(key=lambda r: (-int(r.get('score', 0)), int(r.get('idx', 0))))
        imp2_rows.sort(key=lambda r: (-int(r.get('score', 0)), int(r.get('idx', 0))))

        chosen_rows = []
        for row in imp3_rows:
            if len(chosen_rows) >= max_bullets:
                break
            chosen_rows.append(row)
        for row in imp2_rows:
            if len(chosen_rows) >= max_bullets:
                break
            chosen_rows.append(row)

        if len(chosen_rows) < min_bullets:
            for row in imp2_rows:
                if row in chosen_rows:
                    continue
                chosen_rows.append(row)
                if len(chosen_rows) >= min_bullets or len(chosen_rows) >= max_bullets:
                    break

        chosen_rows.sort(key=lambda r: (-int(r.get('importance', 0)), int(r.get('idx', 0))))
        chosen_objects = [r.get('bullet', {}) for r in chosen_rows]
        chosen_bullets = [b.get('text', '') for b in chosen_objects if b.get('text', '')]

        updated = dict(project)
        updated['bullets'] = chosen_bullets
        updated['bullet_objects'] = chosen_objects
        selected.append(updated)
    return selected


def _normalize_term(value: str) -> str:
    text = normalize_text(value or '').strip().lower()
    text = re.sub(r'\s+', ' ', text)
    return text


def load_resume_json(path) -> dict:
    resume_path = Path(path)
    if not resume_path.exists():
        raise SystemExit(f'Resume JSON file not found: {resume_path}')
    try:
        data = json.loads(resume_path.read_text(encoding='utf-8', errors='replace'))
    except Exception as exc:
        raise SystemExit(f'Failed to parse resume JSON: {resume_path}') from exc
    validate_resume_schema(data)
    return data


def validate_resume_schema(resume_dict: dict) -> None:
    if not isinstance(resume_dict, dict):
        raise SystemExit('Invalid resume schema: root must be a JSON object.')
    required_root = [
        'schema_version', 'basics', 'headline', 'summary', 'education',
        'projects', 'experience', 'skills', 'certifications',
    ]
    for key in required_root:
        if key not in resume_dict:
            raise SystemExit(f'Invalid resume schema: missing root key "{key}".')
    basics = resume_dict.get('basics')
    if not isinstance(basics, dict):
        raise SystemExit('Invalid resume schema: "basics" must be an object.')
    for key in ('name', 'email', 'portfolio', 'github', 'linkedin'):
        if key not in basics:
            raise SystemExit(f'Invalid resume schema: missing basics key "{key}".')
    for key in ('education', 'projects', 'experience', 'certifications'):
        if not isinstance(resume_dict.get(key), list):
            raise SystemExit(f'Invalid resume schema: "{key}" must be an array.')
    skills = resume_dict.get('skills')
    if not isinstance(skills, dict):
        raise SystemExit('Invalid resume schema: "skills" must be an object.')
    for group in SKILL_GROUP_KEYS:
        if group not in skills:
            raise SystemExit(f'Invalid resume schema: missing skills group "{group}".')
        if not isinstance(skills[group], list):
            raise SystemExit(f'Invalid resume schema: skills group "{group}" must be an array.')
    for idx, item in enumerate(resume_dict.get('education', [])):
        if not isinstance(item, dict):
            raise SystemExit(f'Invalid resume schema: education[{idx}] must be an object.')
        for key in ('degree', 'institution', 'start', 'end', 'details'):
            if key not in item:
                raise SystemExit(f'Invalid resume schema: education[{idx}] missing "{key}".')
    for idx, item in enumerate(resume_dict.get('projects', [])):
        if not isinstance(item, dict):
            raise SystemExit(f'Invalid resume schema: projects[{idx}] must be an object.')
        for key in ('name', 'links', 'technologies', 'bullets'):
            if key not in item:
                raise SystemExit(f'Invalid resume schema: projects[{idx}] missing "{key}".')
        if not isinstance(item.get('links'), dict):
            raise SystemExit(f'Invalid resume schema: projects[{idx}].links must be an object.')
        for key in ('github', 'live', 'website'):
            if key not in item['links']:
                raise SystemExit(f'Invalid resume schema: projects[{idx}].links missing "{key}".')
        if not isinstance(item.get('bullets', []), list):
            raise SystemExit(f'Invalid resume schema: projects[{idx}].bullets must be an array.')
        for b_idx, bullet in enumerate(item.get('bullets', [])):
            if isinstance(bullet, str):
                continue
            if not isinstance(bullet, dict):
                raise SystemExit(f'Invalid resume schema: projects[{idx}].bullets[{b_idx}] must be a string or object.')
            for key in ('text', 'core', 'importance', 'tags'):
                if key not in bullet:
                    raise SystemExit(f'Invalid resume schema: projects[{idx}].bullets[{b_idx}] missing "{key}".')
            if not isinstance(bullet.get('tags', []), list):
                raise SystemExit(f'Invalid resume schema: projects[{idx}].bullets[{b_idx}].tags must be an array.')
    for idx, item in enumerate(resume_dict.get('experience', [])):
        if not isinstance(item, dict):
            raise SystemExit(f'Invalid resume schema: experience[{idx}] must be an object.')
        for key in ('title', 'company', 'start', 'end', 'bullets'):
            if key not in item:
                raise SystemExit(f'Invalid resume schema: experience[{idx}] missing "{key}".')
        if not isinstance(item.get('bullets', []), list):
            raise SystemExit(f'Invalid resume schema: experience[{idx}].bullets must be an array.')
        for b_idx, bullet in enumerate(item.get('bullets', [])):
            if isinstance(bullet, str):
                continue
            if not isinstance(bullet, dict):
                raise SystemExit(f'Invalid resume schema: experience[{idx}].bullets[{b_idx}] must be a string or object.')
            for key in ('text', 'core', 'importance', 'tags'):
                if key not in bullet:
                    raise SystemExit(f'Invalid resume schema: experience[{idx}].bullets[{b_idx}] missing "{key}".')


def resume_json_to_text(resume_dict: dict) -> str:
    parts = []
    basics = resume_dict.get('basics', {})
    parts.extend([
        basics.get('name', ''),
        basics.get('email', ''),
        basics.get('portfolio', ''),
        basics.get('github', ''),
        basics.get('linkedin', ''),
        resume_dict.get('headline', ''),
        resume_dict.get('summary', ''),
    ])
    for edu in resume_dict.get('education', []):
        parts.extend([edu.get('degree', ''), edu.get('institution', ''), edu.get('start', ''), edu.get('end', ''), edu.get('details', '')])
    for proj in resume_dict.get('projects', []):
        links = proj.get('links', {})
        parts.extend([proj.get('name', ''), links.get('github', ''), links.get('live', ''), links.get('website', '')])
        parts.extend(proj.get('technologies', []) or [])
        parts.extend(proj.get('bullets', []) or [])
    for exp in resume_dict.get('experience', []):
        parts.extend([exp.get('title', ''), exp.get('company', ''), exp.get('start', ''), exp.get('end', '')])
        parts.extend(exp.get('bullets', []) or [])
    for values in (resume_dict.get('skills', {}) or {}).values():
        parts.extend(values or [])
    parts.extend(resume_dict.get('certifications', []) or [])
    return '\n'.join([normalize_text(str(p)).strip() for p in parts if normalize_text(str(p)).strip()])


def resume_json_to_internal(resume_dict: dict) -> dict:
    skills = []
    for group in SKILL_GROUP_KEYS:
        for skill in resume_dict.get('skills', {}).get(group, []):
            cleaned = clean_skill_token(skill)
            if cleaned:
                skills.append(cleaned)
    dedup_skills = []
    seen = set()
    for s in skills:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup_skills.append(s)

    projects_by_name = {}
    for project in resume_dict.get('projects', []):
        links = project.get('links', {}) or {}
        project_bullets = normalize_bullet_list(project.get('bullets', []) or [])
        projects_by_name[project.get('name', '')] = {
            'title': project.get('name', ''),
            'subtitle': '',
            'links': {
                'github': normalize_text(str(links.get('github', ''))).strip(),
                'live': normalize_text(str(links.get('live', ''))).strip(),
                'website': normalize_text(str(links.get('website', ''))).strip(),
            },
            'date': '',
            'bullets': [b.get('text', '') for b in project_bullets if b.get('text', '')],
            'bullet_objects': project_bullets,
            'technologies': [clean_skill_token(str(t)) for t in (project.get('technologies', []) or []) if clean_skill_token(str(t))],
        }

    experience = []
    for exp in resume_dict.get('experience', []):
        exp_bullets = normalize_bullet_list(exp.get('bullets', []) or [])
        experience.append({
            'title': exp.get('title', ''),
            'subtitle': exp.get('company', ''),
            'date': f'{exp.get("start", "")} - {exp.get("end", "")}'.strip(' -'),
            'bullets': [b.get('text', '') for b in exp_bullets if b.get('text', '')],
            'bullet_objects': exp_bullets,
            'raw': '',
        })

    education = []
    for edu in resume_dict.get('education', []):
        bullets = [normalize_text(edu.get('details', '')).strip()] if normalize_text(edu.get('details', '')).strip() else []
        education.append({
            'title': edu.get('degree', ''),
            'school': edu.get('institution', ''),
            'date': f'{edu.get("start", "")} - {edu.get("end", "")}'.strip(' -'),
            'bullets': bullets,
        })

    return {
        'summary': normalize_text(resume_dict.get('summary', '')).strip(),
        'projects': projects_by_name,
        'skills': dedup_skills,
        'certificates': [normalize_text(str(c)).strip() for c in (resume_dict.get('certifications', []) or []) if normalize_text(str(c)).strip()],
        'experience': experience,
        'education': education,
        'interests': [],
        'name': normalize_text((resume_dict.get('basics', {}) or {}).get('name', '')).strip(),
        'contact': [
            normalize_text((resume_dict.get('basics', {}) or {}).get('email', '')).strip(),
            normalize_text((resume_dict.get('basics', {}) or {}).get('portfolio', '')).strip(),
            normalize_text((resume_dict.get('basics', {}) or {}).get('github', '')).strip(),
            normalize_text((resume_dict.get('basics', {}) or {}).get('linkedin', '')).strip(),
        ],
        'headline': normalize_text(resume_dict.get('headline', '')).strip(),
        'skills_grouped': resume_dict.get('skills', {}),
    }


def build_allowed_terms(resume_text: str) -> set[str]:
    terms: set[str] = set()

    def add(term: str) -> None:
        t = _normalize_term(term)
        if not t:
            return
        terms.add(t)

    sections = split_sections(resume_text)[1]
    skills = parse_skills(sections.get('Skills', []))
    for skill in skills:
        add(skill)
        for part in re.split(r'[/|,]', skill):
            add(part)
        compact = _normalize_term(skill).replace(' ', '')
        if compact and compact != _normalize_term(skill):
            terms.add(compact)

    seed_phrases = [
        'javascript',
        'typescript',
        'react',
        'react native',
        'sql',
        'nosql',
        'rest api integration',
        'rest api',
        'jest',
        'react testing library',
        'bachelor of computer science',
        'job application assistant',
        'production support incident console',
        'bunkerify',
        'cancer awareness mobile app',
    ]
    for phrase in seed_phrases:
        if phrase in _normalize_term(resume_text):
            add(phrase)

    for phrase in re.findall(r'[A-Za-z][A-Za-z0-9\+\#\.\-/ ]{1,45}', normalize_text(resume_text)):
        cleaned = _normalize_term(phrase)
        if len(cleaned) < 2:
            continue
        if cleaned in STOPWORDS:
            continue
        if any(ch.isdigit() for ch in cleaned):
            continue
        if cleaned.startswith('http'):
            continue
        if len(cleaned.split()) > 6:
            continue
        if re.search(r'[a-zA-Z]', cleaned):
            add(cleaned)
    return terms


def _fallback_summary_from_resume(resume_text: str) -> dict:
    summary = (
        "Graduate Software Engineer with a Bachelor of Computer Science majoring in Software Engineering and Cybersecurity. "
        "Hands-on experience building web and mobile applications using JavaScript/TypeScript, React/React Native, SQL/NoSQL, and REST API integration. "
        "Built Job Application Assistant, Production Support Incident Console, Bunkerify, and a Cancer Awareness Mobile App, with application testing using Jest and React Testing Library."
    )
    return {
        'summary': summary,
        'tech_used': [
            'javascript',
            'typescript',
            'react',
            'react native',
            'sql',
            'nosql',
            'rest api integration',
            'jest',
            'react testing library',
        ],
        'evidence_phrases': [
            'Bachelor of Computer Science',
            'JavaScript/TypeScript',
            'React/React Native',
            'SQL/NoSQL',
            'REST API integrations',
            'Job Application Assistant',
            'Production Support Incident Console',
            'Bunkerify',
            'Cancer Awareness Mobile App',
            'Jest/React Testing Library',
        ],
        'source': 'fallback',
    }


def _validate_summary_payload(payload: dict, resume_text: str, allowed_terms: set[str]) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, 'payload not a dict'
    summary = normalize_text(str(payload.get('summary', ''))).strip()
    if not summary:
        return False, 'missing summary'
    tech_used = payload.get('tech_used', [])
    if not isinstance(tech_used, list):
        return False, 'tech_used must be a list'
    for item in tech_used:
        term = _normalize_term(str(item))
        if not term or term not in allowed_terms:
            return False, f'tech term not allowed: {item}'
    evidence = payload.get('evidence_phrases', [])
    if not isinstance(evidence, list):
        return False, 'evidence_phrases must be a list'
    for phrase in evidence:
        raw = str(phrase)
        if not raw:
            continue
        if raw not in resume_text:
            return False, f'evidence phrase not found in resume: {raw}'

    if SUMMARY_BANNED_RE.search(summary):
        banned_terms = {'c#', 'c sharp', '.net', 'asp.net', 'aspnet', 'dotnet'}
        if not any(t in allowed_terms for t in banned_terms):
            return False, 'summary contains banned technology term'
    return True, ''


def _generate_summary_with_ai(
    job_text: str,
    resume_text: str,
    fallback_summary: str,
    allowed_terms: set[str],
    classification: dict,
    strategy: dict,
    stronger_warning: bool = False,
) -> dict:
    warning = ''
    if stronger_warning:
        warning = (
            "CRITICAL: Previous output failed validation. "
            "If any term is not in allowlist or any evidence phrase is not verbatim in resume_text, the output will be discarded.\n"
        )
    prompt = (
        f"Job description:\n{_truncate(job_text or '', 2500)}\n\n"
        f"Current summary:\n{_truncate(fallback_summary or '', 500)}\n\n"
        f"Classification:\n{json.dumps(classification or {}, ensure_ascii=False)}\n\n"
        f"Strategy:\n{json.dumps(strategy or {}, ensure_ascii=False)}\n\n"
        f"Allowlist terms:\n{json.dumps(sorted(allowed_terms), ensure_ascii=False)}\n\n"
        f"resume_text:\n{_truncate(resume_text, 6500)}\n"
    )
    instructions = (
        "Return STRICT JSON only with this exact shape:\n"
        "{"
        "\"summary\": \"...\","
        "\"tech_used\": [\"...\"],"
        "\"evidence_phrases\": [\"...\"]"
        "}\n"
        "Rules:\n"
        "- Do not make assumptions.\n"
        "- tech_used must contain only terms from the provided allowlist.\n"
        "- evidence_phrases must be exact substrings copied from resume_text.\n"
        "- Do not introduce technologies not present in allowlist.\n"
        "- Keep summary concise (45-65 words).\n"
        "- Do not include markdown or extra keys.\n"
        f"{warning}"
    )
    raw = _call_ai_text(prompt=prompt, instructions=instructions, max_tokens=700, model_tier='fast')
    match = re.search(r'\{[\s\S]*\}', raw or '')
    payload = match.group(0) if match else (raw or '{}')
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError('summary AI output is not a JSON object')
    return data


def generate_summary_with_guard(
    job_text: str,
    resume_text: str,
    fallback_summary: str,
    classification: dict,
    strategy: dict,
) -> dict:
    allowed_terms = build_allowed_terms(resume_text)
    if not _get_ai_provider() or not (job_text or '').strip():
        fallback = _fallback_summary_from_resume(resume_text)
        fallback['summary'] = _sanitize_summary_text(fallback['summary'], fallback=fallback_summary, max_words=65)
        return fallback

    for attempt in range(2):
        try:
            candidate = _generate_summary_with_ai(
                job_text=job_text,
                resume_text=resume_text,
                fallback_summary=fallback_summary,
                allowed_terms=allowed_terms,
                classification=classification,
                strategy=strategy,
                stronger_warning=(attempt == 1),
            )
            ok, _reason = _validate_summary_payload(candidate, resume_text=resume_text, allowed_terms=allowed_terms)
            if ok:
                return {
                    'summary': _sanitize_summary_text(str(candidate.get('summary', '')), fallback=fallback_summary, max_words=65),
                    'tech_used': [_normalize_term(t) for t in candidate.get('tech_used', []) if _normalize_term(str(t))],
                    'evidence_phrases': [str(p) for p in candidate.get('evidence_phrases', []) if str(p)],
                    'source': 'ai',
                }
        except Exception:
            pass

    fallback = _fallback_summary_from_resume(resume_text)
    fallback['summary'] = _sanitize_summary_text(fallback['summary'], fallback=fallback_summary, max_words=65)
    return fallback


def filter_skills_for_job(skills, job_text, max_skills=16, min_skills=10, prefer_cyber_terms=False):
    if not skills:
        return skills
    if not job_text:
        return skills[:max_skills]

    job_norm = normalize_text(job_text).lower()
    job_words = set(
        w for w in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', job_norm)
        if len(w) >= 3 and w not in STOPWORDS
    )

    cyber_focused_role = any(term in job_norm for term in CYBER_JOB_TERMS) or bool(prefer_cyber_terms)

    def is_cyber_tool_skill(skill_text: str) -> bool:
        skill_norm = normalize_text(skill_text).lower()
        return any(term in skill_norm for term in CYBER_TOOL_TERMS)

    scored = []
    for idx, skill in enumerate(skills):
        s_norm = normalize_text(skill).lower()
        score = 0
        if s_norm and s_norm in job_norm:
            score += 5
        for token in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', s_norm):
            if token in job_words:
                score += 1
        if cyber_focused_role and is_cyber_tool_skill(skill):
            score += 2
        if prefer_cyber_terms and is_cyber_tool_skill(skill):
            score += 1
        scored.append((score, idx, skill))

    matches = [t for t in scored if t[0] > 0]
    selected = []
    selected_keys = set()

    if matches:
        matches.sort(key=lambda t: (-t[0], t[1]))
        for _, _, skill in matches:
            key = skill.lower()
            if key not in selected_keys:
                selected.append(skill)
                selected_keys.add(key)
            if len(selected) >= max_skills:
                break

    # Pad with existing resume skills to avoid an overly sparse section.
    for skill in skills:
        key = skill.lower()
        if key in selected_keys:
            continue
        if len(selected) >= max_skills:
            break
        selected.append(skill)
        selected_keys.add(key)
        if len(selected) >= min_skills and matches:
            # If we already have matches, stop after reaching a healthy count.
            break

    if cyber_focused_role:
        prioritized_cyber = []
        seen = set(s.lower() for s in selected)
        for skill in skills:
            if len(prioritized_cyber) >= 6:
                break
            if is_cyber_tool_skill(skill) and skill.lower() not in seen:
                prioritized_cyber.append(skill)
                seen.add(skill.lower())
        if prioritized_cyber:
            selected = prioritized_cyber + selected

    return selected[:max_skills]


def _format_contact_item(item: str) -> str:
    raw = (item or '').strip()
    if not raw:
        return ''
    low = raw.lower()

    if low.startswith(('http://', 'https://', 'www.')):
        href = raw if low.startswith(('http://', 'https://')) else f'https://{raw}'
        label = 'Link'
        if 'github.com' in low:
            label = '🐙 GitHub'
        elif 'linkedin.com' in low:
            label = '💼 LinkedIn'
        elif 'mobeenkhan.com' in low:
            domain = re.sub(r'^https?://', '', raw, flags=re.IGNORECASE).strip('/')
            label = f'🌐 {domain or "mobeenkhan.com"}'
        else:
            domain = re.sub(r'^https?://', '', raw, flags=re.IGNORECASE).strip('/')
            if domain:
                label = domain
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    if '@' in raw and ' ' not in raw:
        return f'📧 {html.escape(raw)}'

    if re.search(r'^\+?[0-9][0-9\s\-]{6,}$', raw):
        return html.escape(raw)

    if 'sydney' in low:
        return html.escape(raw)

    return html.escape(raw)


def render_html(
    name,
    headline,
    contact,
    summary,
    education,
    skills,
    projects,
    experience,
    volunteer,
    certificates,
    interests,
    keywords,
    style_css,
    header_html=None,
    section_order=None,
    grouped_skills=None,
    skill_priority_groups=None,
):
    if not education:
        raise SystemExit('Resume rendering error: Education section is empty.')
    if not projects:
        raise SystemExit('Resume rendering error: Projects section is empty.')
    if not (experience or volunteer):
        raise SystemExit('Resume rendering error: Professional Experience section is empty.')

    contact_items = []
    for c in contact:
        formatted = _format_contact_item(c)
        if formatted:
            contact_items.append(formatted)

    tagline = headline

    summary_text = summary
    if summary_text and summary_text[-1] not in '.!?':
        summary_text += '.'

    def join_contact(items):
        if not items:
            return ''
        parts = []
        for i, item in enumerate(items):
            parts.append(item)
        return ' <span>|</span> '.join(parts)

    def render_entries(entries, with_subtitle=False, entry_class='entry'):
        def render_project_links(links: dict) -> str:
            if not isinstance(links, dict):
                return ''
            parts = []
            for key, icon, label in (
                ('github', '🐙', 'GitHub'),
                ('live', '🌐', 'Live'),
                ('website', '🔗', 'Website'),
            ):
                url = normalize_text(str(links.get(key, ''))).strip()
                if not url:
                    continue
                href = url if url.lower().startswith(('http://', 'https://')) else f'https://{url}'
                parts.append(f'{icon} <a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener">{label}</a>')
            return ' | '.join(parts)

        html_parts = []
        for e in entries:
            html_parts.append(f'<div class="{entry_class}">')
            html_parts.append('<div class="entry-header">')
            html_parts.append(f'<span class="entry-title">{linkify_text_compact_links(e.get("title", ""))}</span>')
            if e.get('date'):
                html_parts.append(f'<span class="entry-date">{e["date"]}</span>')
            html_parts.append('</div>')
            links_html = render_project_links(e.get('links', {}))
            if links_html:
                html_parts.append(f'<div class="entry-subtitle">{links_html}</div>')
            if with_subtitle and e.get('school'):
                html_parts.append(f'<div class="entry-subtitle">{linkify_text(e["school"])}</div>')
            elif e.get('subtitle'):
                html_parts.append(f'<div class="entry-subtitle">{linkify_text_compact_links(e["subtitle"])}</div>')
            if e.get('bullets'):
                html_parts.append('<ul>')
                for b in e['bullets']:
                    html_parts.append(f'<li>{linkify_text(b)}</li>')
                html_parts.append('</ul>')
            html_parts.append('</div>')
        return '\n'.join(html_parts)
    edu_html = render_entries(education, with_subtitle=True)
    skills_html = ''.join([f'<span class="skill-tag">{html.escape(s)}</span>' for s in (skills or []) if s])
    proj_html = render_entries(projects, entry_class='entry project')
    combined_experience = (experience or []) + (volunteer or [])
    exp_html = render_entries(combined_experience)
    cert_items = ''.join([f'<li>{linkify_text(c)}</li>' for c in certificates]) if certificates else ''

    section_html_map = {
        'Professional Summary': (
            '<div class="section"><div class="section-title">Professional Summary</div>'
            f'<p class="summary">{linkify_text(summary_text)}</p></div>'
        ),
        'Key Skills / Technical Skills': (
            '<div class="section"><div class="section-title">Key Skills</div>'
            f'<div class="skills-grid">{skills_html}</div></div>'
        ),
        'Professional Experience': (
            '<div class="section"><div class="section-title">Professional Experience</div>'
            f'{exp_html}</div>'
        ),
        'Projects': (
            '<div class="section section-projects"><div class="section-title">Projects</div>'
            f'{proj_html}</div>'
        ),
        'Education': (
            '<div class="section"><div class="section-title">Education</div>'
            f'{edu_html}</div>'
        ),
        'Certifications': (
            '<div class="section"><div class="section-title">Certificates</div>'
            f'<ul>{cert_items}</ul></div>' if cert_items else ''
        ),
    }
    ordered_titles = section_order[:] if section_order else DEFAULT_SECTION_ORDER[:]
    body_sections = []
    for title in ordered_titles:
        block = section_html_map.get(title)
        if block:
            body_sections.append(block)

    header_block = header_html if header_html else f"""
<div class="header">
  <h1>{name}</h1>
  <div class="tagline">{tagline}</div>
  <div class="contact-row">{join_contact(contact_items)}</div>
</div>
"""

    html_doc = f"""<!DOCTYPE html>
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
{''.join(body_sections)}

</div>
</body>
</html>
"""
    return html_doc


def render_header_html(name: str, headline: str, contact):
    contact_items = []
    for c in contact:
        formatted = _format_contact_item(c)
        if formatted:
            contact_items.append(formatted)

    def join_contact(items):
        if not items:
            return ''
        parts = []
        for item in items:
            parts.append(item)
        return ' <span>|</span> '.join(parts)

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


def extract_template_skill_tags(template_text: str) -> list[str]:
    tags = re.findall(
        r'<span\s+class="skill-tag"\s*>\s*(.*?)\s*</span>',
        template_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    out = []
    seen = set()
    for raw in tags:
        text = re.sub(r'<.*?>', '', raw or '').strip()
        skill = clean_skill_token(text)
        if not skill:
            continue
        key = skill.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(skill)
    return out


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
        if re.fullmatch(r'#+', line):
            continue

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

        if line in ('---', 'â€”', '--'):
            continue

        if line.startswith('- ') or line.startswith('* '):
            ensure_section()
            item = line[2:].strip()
            if current['title'].lower().find('skill') >= 0:
                if ':' in item:
                    item = item.split(':', 1)[1].strip()
                for part in _split_skills_csv(item):
                    cleaned = clean_skill_token(part)
                    if cleaned:
                        current['skills'].append(cleaned)
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


def _ensure_section(sections, title: str):
    for section in sections:
        if section.get('title', '').strip().lower() == title.lower():
            return section
    section = {
        'title': title,
        'entries': [],
        'bullets': [],
        'paragraphs': [],
        'skills': [],
    }
    sections.append(section)
    return section


def _is_driving_role(entry: dict) -> bool:
    haystack = ' '.join([
        normalize_text(str(entry.get('title', ''))).lower(),
        normalize_text(str(entry.get('subtitle', ''))).lower(),
        normalize_text(str(entry.get('date', ''))).lower(),
        ' '.join(normalize_text(str(b)).lower() for b in entry.get('bullets', [])),
    ])
    return any(k in haystack for k in ('delivery driver', 'rideshare driver', 'driver'))


def _project_rank(entry_title: str) -> int | None:
    title_l = normalize_text(entry_title).lower()
    order = {
        'bunkerify': 0,
        'production support incident console': 1,
        'incident console': 1,
        'job application assistant': 2,
        'resume tailor': 2,
        'cancer awareness': 3,
        'cancer awareness mobile app': 3,
    }
    for key, rank in order.items():
        if key in title_l:
            return rank
    return None


def _normalize_additional_entry_title(title: str) -> str:
    t = normalize_text(title).strip().lower()
    # Remove trailing date ranges when AI inlines them into the title.
    t = re.sub(
        r'\b(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|'
        r'sep|sept|september|oct|october|nov|november|dec|december)\s+\d{4}\s*-\s*'
        r'(?:present|'
        r'(?:jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|aug|august|'
        r'sep|sept|september|oct|october|nov|november|dec|december)\s+\d{4})\b',
        '',
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r'\b\d{2}/\d{4}\s*-\s*(?:present|\d{2}/\d{4})\b', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s+', ' ', t).strip(' -|')
    return t


def _prioritize_tailored_sections(sections, fallback_driving_entries=None, fallback_project_entries=None):
    title_map = {
        'key skills': 'Key Skills / Technical Skills',
        'technical skills': 'Key Skills / Technical Skills',
        'skills': 'Key Skills / Technical Skills',
        'certificates': 'Certifications',
        'work experience': 'Professional Experience',
        'experience': 'Professional Experience',
    }
    for section in sections:
        key = normalize_text(section.get('title', '')).strip().lower()
        if key in title_map:
            section['title'] = title_map[key]

    prof = _ensure_section(sections, 'Professional Experience')
    additional = _ensure_section(sections, 'Additional Information')

    kept_entries = []
    moved_driving = []
    for entry in prof.get('entries', []):
        title_l = normalize_text(entry.get('title', '')).lower()
        subtitle_l = normalize_text(entry.get('subtitle', '')).lower()
        if _is_driving_role(entry):
            moved_driving.append(entry)
            continue
        if ('catch a drive' in title_l or 'hentley' in title_l or
                'catch a drive' in subtitle_l or 'hentley' in subtitle_l):
            kept_entries.append(entry)
    prof['entries'] = kept_entries

    # If AI omitted driving roles entirely, recover them from source resume entries.
    if not moved_driving and fallback_driving_entries:
        moved_driving = [dict(e) for e in fallback_driving_entries if _is_driving_role(e)]

    if moved_driving:
        seen = set()
        for e in additional['entries']:
            seen.add((
                _normalize_additional_entry_title(e.get('title', '')),
                normalize_text(e.get('date', '')).lower(),
            ))
        for entry in moved_driving:
            key = (
                _normalize_additional_entry_title(entry.get('title', '')),
                normalize_text(entry.get('date', '')).lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            additional['entries'].append(entry)

    # Final dedup of ALL Additional Information entries.
    # Use title/subtitle/date plus bullet content to catch repeated entries even
    # when one duplicate is missing a date.
    seen_add: set[tuple[str, str, str, str]] = set()
    seen_add_loose: set[tuple[str, str, str]] = set()
    deduped_add = []
    for e in additional['entries']:
        bullets_norm = ' | '.join(
            normalize_text(str(b)).strip().lower() for b in (e.get('bullets', []) or [])
        )
        key = (
            _normalize_additional_entry_title(e.get('title', '')),
            normalize_text(e.get('subtitle', '')).lower(),
            normalize_text(e.get('date', '')).lower(),
            bullets_norm,
        )
        loose_key = (
            _normalize_additional_entry_title(e.get('title', '')),
            normalize_text(e.get('subtitle', '')).lower(),
            bullets_norm,
        )
        if key in seen_add or loose_key in seen_add_loose:
            continue
        # Also dedupe bullets in each retained entry.
        if e.get('bullets'):
            seen_bullets = set()
            unique_bullets = []
            for b in e.get('bullets', []):
                b_key = normalize_text(str(b)).strip().lower()
                if b_key in seen_bullets:
                    continue
                seen_bullets.add(b_key)
                unique_bullets.append(b)
            e['bullets'] = unique_bullets
        seen_add.add(key)
        seen_add_loose.add(loose_key)
        deduped_add.append(e)
    additional['entries'] = deduped_add

    projects = _ensure_section(sections, 'Projects')
    # Build set of project ranks already present in AI output.
    existing_project_keys: set[int] = set()
    for entry in projects.get('entries', []):
        rank = _project_rank(entry.get('title', ''))
        if rank is not None:
            existing_project_keys.add(rank)

    # Inject any ranked projects the AI omitted.
    if fallback_project_entries:
        for entry in fallback_project_entries:
            rank = _project_rank(entry.get('title', ''))
            if rank is None or rank in existing_project_keys:
                continue
            projects['entries'].append(dict(entry))
            existing_project_keys.add(rank)

    # Unconditional: Job Application Assistant (rank=2) and Cancer Awareness
    # (rank=3) must always appear â€” force-inject from fallback if still missing.
    for must_rank in sorted({2, 3} - existing_project_keys):
        for entry in (fallback_project_entries or []):
            if _project_rank(entry.get('title', '')) == must_rank:
                projects['entries'].append(dict(entry))
                existing_project_keys.add(must_rank)
                break

    if projects.get('entries'):
        def project_key(entry):
            rank = _project_rank(entry.get('title', ''))
            title_l = normalize_text(entry.get('title', '')).lower()
            return (rank if rank is not None else 99, title_l)

        projects['entries'] = sorted(projects['entries'], key=project_key)

    education = _ensure_section(sections, 'Education')
    if education.get('entries'):
        seen_edu: set[str] = set()
        deduped_edu = []
        for e in education['entries']:
            key = normalize_text(e.get('title', '')).lower()
            if key not in seen_edu:
                seen_edu.add(key)
                deduped_edu.append(e)
        education['entries'] = deduped_edu

        def edu_key(entry):
            title_l = normalize_text(entry.get('title', '')).lower()
            if 'bachelor' in title_l:
                return (0, title_l)
            if 'master' in title_l:
                return (1, title_l)
            return (2, title_l)
        education['entries'] = sorted(education['entries'], key=edu_key)

    certs = _ensure_section(sections, 'Certifications')
    seen_cert: set[str] = set()
    deduped_certs = []
    for e in certs.get('entries', []):
        key = normalize_text(e.get('title', '')).lower()
        if key not in seen_cert:
            seen_cert.add(key)
            deduped_certs.append(e)
    certs['entries'] = deduped_certs
    cert_blob = ' '.join(certs.get('paragraphs', []) + certs.get('bullets', []))
    for e in certs['entries']:
        cert_blob += ' ' + e.get('title', '') + ' ' + e.get('subtitle', '')
    if 'aws academy cloud foundations' not in normalize_text(cert_blob).lower():
        certs['bullets'].append('AWS Academy Graduate - AWS Academy Cloud Foundations award')

    # Keep only target section titles and return in exact order.
    by_title = {}
    for section in sections:
        key = normalize_text(section.get('title', '')).strip().lower()
        if key not in by_title and key in {t.lower() for t in TAILORED_SECTION_TITLES}:
            by_title[key] = section
    ordered = []
    for title in TAILORED_SECTION_TITLES:
        section = by_title.get(title.lower())
        if section:
            ordered.append(section)
    return ordered


def _collect_required_project_entries(source_sections: dict) -> list[dict]:
    candidates = []
    for key in ('Work experience/Projects', 'Projects'):
        candidates.extend(parse_experience(source_sections.get(key, [])))
    seen = set()
    ranked = []
    for entry in candidates:
        rank = _project_rank(entry.get('title', ''))
        if rank is None:
            continue
        sig = (rank, normalize_text(entry.get('title', '')).lower())
        if sig in seen:
            continue
        seen.add(sig)
        ranked.append(dict(entry))
    return ranked


def _clamp_professional_summary(sections, max_words: int = 65) -> None:
    section = _ensure_section(sections, 'Professional Summary')
    if not section.get('paragraphs'):
        return
    text = ' '.join(p.strip() for p in section['paragraphs'] if p.strip())
    if not text:
        section['paragraphs'] = []
        return
    words = text.split()
    if len(words) > max_words:
        text = ' '.join(words[:max_words]).rstrip(' ,;:')
        if text and text[-1] not in '.!?':
            text += '.'
    section['paragraphs'] = [text]


def _apply_canonical_skills_to_sections(sections, canonical_skills: list[str], job_text: str) -> None:
    if not canonical_skills:
        return
    filtered = filter_skills_for_job(
        canonical_skills,
        job_text=job_text or '',
        max_skills=16,
        min_skills=10,
    )
    for section in sections:
        if 'skill' in section.get('title', '').lower():
            section['skills'] = filtered[:]


def _entry_match_key(text: str) -> str:
    value = normalize_text(html.unescape(text or '')).lower()
    value = re.sub(r'[^a-z0-9]+', ' ', value).strip()
    return re.sub(r'\s+', ' ', value)


def _sanitize_summary_text(summary: str, fallback: str, max_words: int = 65) -> str:
    text = normalize_text(summary or '').strip()
    if not text:
        text = normalize_text(fallback or '').strip()
    words = text.split()
    if len(words) > max_words:
        text = ' '.join(words[:max_words]).rstrip(' ,;:')
    if text and text[-1] not in '.!?':
        text += '.'
    return text


def _sanitize_skills_from_ai(
    ai_skills: list[str],
    canonical_skills: list[str],
    job_text: str,
    max_skills: int = 16,
    min_skills: int = 10,
    prefer_cyber_terms: bool = False,
) -> list[str]:
    canonical = [clean_skill_token(s) for s in canonical_skills if clean_skill_token(s)]
    if not canonical:
        return []
    canonical_map = {_entry_match_key(s): s for s in canonical}
    selected = []
    seen = set()
    for raw in ai_skills or []:
        key = _entry_match_key(raw)
        if not key:
            continue
        skill = canonical_map.get(key)
        if not skill:
            continue
        low = skill.lower()
        if low in seen:
            continue
        seen.add(low)
        selected.append(skill)
    if not selected:
        selected = canonical[:]
    return filter_skills_for_job(
        selected,
        job_text=job_text or '',
        max_skills=max_skills,
        min_skills=min_skills,
        prefer_cyber_terms=prefer_cyber_terms,
    )


_TECH_PHRASE_HINTS = [
    'javascript', 'typescript', 'react', 'react native', 'next.js', 'tailwind css',
    'framer motion', 'react router', 'firebase', 'cloud firestore', 'rest api',
    'api', 'sql', 'nosql', 'pl/sql', 'python', 'java', 'php', 'c++',
    'kali linux', 'nmap', 'metasploit', 'wireshark', 'burp suite',
    'incident response', 'threat intelligence', 'vulnerability assessment',
    'penetration testing', 'network segmentation', 'firewall configuration',
    'jest', 'react testing library', 'axios', 'vite', 'sendgrid',
    'aws', 'amazon web services', 'azure', 'gcp', 'google cloud', 'asp.net', '.net', 'dotnet', 'c#',
]

_CLOUD_PROVIDER_VARIANTS = {
    'aws': {'aws', 'amazon web services'},
    'azure': {'azure', 'microsoft azure'},
    'gcp': {'gcp', 'google cloud', 'google cloud platform'},
    'firebase': {'firebase', 'cloud firestore'},
}
BULLET_SIMILARITY_THRESHOLD = 0.55


def _extract_tech_terms(text: str) -> set[str]:
    norm = _normalize_term(text)
    if not norm:
        return set()
    found = set()
    for phrase in _TECH_PHRASE_HINTS:
        p = _normalize_term(phrase)
        if p and p in norm:
            found.add(p)
    for token in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\./-]{1,30}', norm):
        token_n = _normalize_term(token).strip('.,;:!?')
        if not token_n:
            continue
        techy = (
            any(ch in token_n for ch in ('+', '#', '/')) or
            token_n in {'api', 'sql', 'nosql', 'firebase', 'react', 'typescript', 'javascript', 'aws', 'azure', 'gcp'}
        )
        if techy:
            found.add(token_n)
    return found


def _tokenize_similarity(text: str) -> set[str]:
    return {
        t for t in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\./-]+', _normalize_term(text))
        if len(t) >= 2 and t not in STOPWORDS
    }


def _bullet_similarity(a: str, b: str) -> float:
    a_n = _normalize_term(a)
    b_n = _normalize_term(b)
    if not a_n or not b_n:
        return 0.0
    seq = difflib.SequenceMatcher(None, a_n, b_n).ratio()
    a_t = _tokenize_similarity(a_n)
    b_t = _tokenize_similarity(b_n)
    inter = len(a_t & b_t)
    union = len(a_t | b_t)
    jaccard = (inter / union) if union else 0.0
    return max(seq, jaccard)


def _extract_capitalized_tech_candidates(lines: list[str]) -> set[str]:
    out = set()
    ignore = {
        'built', 'implemented', 'added', 'delivered', 'managed', 'designed', 'developed',
        'created', 'enhanced', 'supported', 'ensured', 'collaborated', 'maintained',
    }
    for line in lines:
        for token in re.findall(r'\b[A-Z][A-Za-z0-9\+#\.-]{1,}\b', normalize_text(line or '')):
            t = token.strip()
            low = t.lower()
            if low in ignore:
                continue
            looks_techy = any(ch in t for ch in '+#.-') or len(t) >= 3
            if looks_techy:
                out.add(low)
    return out


def _numeric_tokens(text: str) -> set[str]:
    return set(re.findall(r'\b\d+(?:\.\d+)?%?\b', normalize_text(text or '')))


def _validate_project_bullets_constrained(
    generated_bullets: list[str],
    original_bullets: list[str],
    allowed_terms: set[str],
) -> tuple[bool, str]:
    if not generated_bullets:
        return True, ''
    if len(generated_bullets) > len(original_bullets):
        return False, 'project update added new bullet points'

    gen_terms = _extract_tech_terms(' '.join(generated_bullets))
    disallowed = sorted(t for t in gen_terms if t not in allowed_terms)
    if disallowed:
        return False, f'project tech terms not in project allowlist: {", ".join(disallowed[:5])}'

    orig_caps = _extract_capitalized_tech_candidates(original_bullets)
    gen_caps = _extract_capitalized_tech_candidates(generated_bullets)
    new_caps = sorted(c for c in gen_caps if c not in orig_caps)
    if new_caps:
        return False, f'new capitalized tech terms detected: {", ".join(new_caps[:5])}'

    unmatched = list(range(len(original_bullets)))
    for bullet in generated_bullets:
        best_idx = None
        best_score = 0.0
        for idx in unmatched:
            score = _bullet_similarity(bullet, original_bullets[idx])
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx is None or best_score < BULLET_SIMILARITY_THRESHOLD:
            return False, f'bullet semantic drift (score={best_score:.2f})'
        gen_nums = _numeric_tokens(bullet)
        orig_nums = _numeric_tokens(original_bullets[best_idx])
        if gen_nums - orig_nums:
            return False, 'new metrics or numeric claims introduced'
        unmatched.remove(best_idx)

    orig_providers = _cloud_providers_in_terms(_extract_tech_terms(' '.join(original_bullets)))
    gen_providers = _cloud_providers_in_terms(gen_terms)
    substituted = sorted(p for p in gen_providers if p not in orig_providers)
    if substituted:
        return False, f'cloud provider substitution: {", ".join(substituted)}'
    return True, ''


def _extract_source_index_bullets(project: dict) -> list[dict]:
    rows = []
    texts = bullet_texts(project.get('bullet_objects', project.get('bullets', [])))
    for idx, bullet in enumerate(texts):
        rows.append({'source_index': idx, 'text': normalize_text(str(bullet)).strip()})
    return rows


def _project_tech_allowlist_from_resume_json(project: dict) -> set[str]:
    allowed = set()
    for term in project.get('technologies', []) or []:
        t = _normalize_term(str(term))
        if t:
            allowed.add(t)
            allowed.add(t.replace(' ', ''))
            for token in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\./-]+', t):
                allowed.add(_normalize_term(token))
            for inferred in _extract_tech_terms(t):
                allowed.add(_normalize_term(inferred))
    bullet_rows = normalize_bullet_list(project.get('bullets', []) or [])
    bullet_text_blob = ' '.join([b.get('text', '') for b in bullet_rows if b.get('text', '')])
    for inferred in _extract_tech_terms(bullet_text_blob):
        t = _normalize_term(inferred)
        if t:
            allowed.add(t)
    for generic in GENERIC_BULLET_TERMS:
        allowed.add(_normalize_term(generic))
    return allowed


def _validate_light_rephrase_payload(payload: dict, selected_projects: list[dict]) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, 'AI payload is not an object'
    projects_data = payload.get('projects', [])
    if not isinstance(projects_data, list):
        return False, 'AI payload projects must be an array'
    by_name = {_entry_match_key(p.get('title', p.get('name', ''))): p for p in selected_projects}
    for project_item in projects_data:
        if not isinstance(project_item, dict):
            return False, 'AI project payload item must be an object'
        key = _entry_match_key(project_item.get('name', ''))
        original_project = by_name.get(key)
        if not original_project:
            return False, f'AI returned unknown project: {project_item.get("name", "")}'
        src_bullets = [normalize_text(str(b)).strip() for b in original_project.get('bullets', []) if normalize_text(str(b)).strip()]
        returned = project_item.get('bullets', [])
        if not isinstance(returned, list):
            return False, f'AI bullets for {project_item.get("name", "")} must be an array'
        if len(returned) > len(src_bullets):
            return False, f'AI added bullets for {project_item.get("name", "")}'
        seen_indexes = set()
        rewritten_texts = []
        for row in returned:
            if not isinstance(row, dict):
                return False, f'AI bullet row for {project_item.get("name", "")} must be an object'
            src_idx = row.get('source_index')
            if not isinstance(src_idx, int):
                return False, f'AI source_index missing/int for {project_item.get("name", "")}'
            if src_idx < 0 or src_idx >= len(src_bullets):
                return False, f'AI source_index out of range for {project_item.get("name", "")}'
            if src_idx in seen_indexes:
                return False, f'AI duplicate source_index for {project_item.get("name", "")}'
            seen_indexes.add(src_idx)
            new_text = normalize_text(str(row.get('text', ''))).strip()
            if not new_text:
                return False, f'AI empty bullet text for {project_item.get("name", "")}'
            sim = _bullet_similarity(new_text, src_bullets[src_idx])
            if sim < BULLET_SIMILARITY_THRESHOLD:
                return False, f'AI bullet drift too high for {project_item.get("name", "")}'
            rewritten_texts.append(new_text)

        allowed_terms = _project_tech_allowlist_from_resume_json(original_project)
        tech_terms = _extract_tech_terms(' '.join(rewritten_texts))
        disallowed = [t for t in tech_terms if t not in allowed_terms]
        if disallowed:
            return False, f'AI introduced non-allowed project technology terms in {project_item.get("name", "")}'

        orig_terms = _extract_tech_terms(' '.join(src_bullets))
        orig_providers = _cloud_providers_in_terms(orig_terms | allowed_terms)
        gen_providers = _cloud_providers_in_terms(tech_terms)
        substituted = [p for p in gen_providers if p not in orig_providers]
        if substituted:
            return False, f'AI cloud provider substitution in {project_item.get("name", "")}'
    return True, ''


def light_rephrase_selected_content_with_ai(job_text: str, summary: str, selected_projects: list[dict]) -> dict:
    if not _get_ai_provider() or not (job_text or '').strip():
        return {}
    payload = {
        'summary': summary,
        'projects': [
            {
                'name': p.get('title', p.get('name', '')),
                'technologies': p.get('technologies', []),
                'bullets': _extract_source_index_bullets(p),
            }
            for p in selected_projects
        ],
    }
    instructions = (
        "Return STRICT JSON only:\n"
        "{"
        "\"summary\":\"...\","
        "\"projects\":[{\"name\":\"...\",\"bullets\":[{\"source_index\":0,\"text\":\"...\"}]}]"
        "}\n"
        "Rules:\n"
        "- You may only lightly rephrase provided summary and bullets.\n"
        "- You may reorder bullets by returning source_index entries in any order.\n"
        "- You may remove bullets by omitting source_index rows.\n"
        "- You may NOT create new bullets.\n"
        "- You may NOT introduce new claims, technologies, metrics, or providers.\n"
        "- Each bullet must map to an existing bullet via source_index.\n"
    )
    text = _call_ai_text(
        prompt=(
            f"Job description:\n{job_text}\n\n"
            f"Selected resume content:\n{json.dumps(payload, ensure_ascii=False)}\n"
        ),
        instructions=instructions,
        max_tokens=1600,
        model_tier='fast',
    )
    match = re.search(r'\{[\s\S]*\}', text or '')
    raw = match.group(0) if match else (text or '{}')
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    ok, _reason = _validate_light_rephrase_payload(data, selected_projects=selected_projects)
    if not ok:
        return {}
    return data


def build_project_allowed_terms(projects_by_name: dict[str, dict]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for project_name, entry in (projects_by_name or {}).items():
        key = _entry_match_key(project_name)
        blob_parts = [entry.get('title', ''), entry.get('subtitle', ''), ' '.join(entry.get('bullets', []))]
        terms = _extract_tech_terms(' '.join([str(p) for p in blob_parts if p]))
        out[key] = terms
    return out


def _cloud_providers_in_terms(terms: set[str]) -> set[str]:
    providers = set()
    for provider, variants in _CLOUD_PROVIDER_VARIANTS.items():
        if any(_normalize_term(v) in terms for v in variants):
            providers.add(provider)
    return providers


def _validate_project_updates_section_scoped(
    project_updates,
    project_allowed_terms_by_title: dict[str, set[str]],
    original_project_entries_by_key: dict[str, dict],
    original_project_keys: set[str],
) -> tuple[bool, list[dict], str]:
    if not isinstance(project_updates, list):
        return True, [], ''
    cleaned_updates = []
    for item in project_updates:
        if not isinstance(item, dict):
            continue
        title = str(item.get('title', ''))
        key = _entry_match_key(title)
        bullets = item.get('bullets', [])
        if key not in original_project_keys:
            continue
        if not isinstance(bullets, list):
            return False, [], f'invalid bullets for project: {title}'
        normalized_bullets = [normalize_text(str(b)).strip() for b in bullets if normalize_text(str(b)).strip()]
        allowed_terms = project_allowed_terms_by_title.get(key, set())
        original_entry = original_project_entries_by_key.get(key, {})
        original_bullets = [normalize_text(str(b)).strip() for b in original_entry.get('bullets', []) if normalize_text(str(b)).strip()]
        valid, reason = _validate_project_bullets_constrained(
            generated_bullets=normalized_bullets,
            original_bullets=original_bullets,
            allowed_terms=allowed_terms,
        )
        if not valid:
            return False, [], f'{title}: {reason}'

        cleaned_updates.append({'title': title, 'bullets': normalized_bullets[:6]})
    return True, cleaned_updates, ''


def validate_generated_resume(original_resume_json: dict, generated_resume_json: dict) -> None:
    def names(items, key):
        out = set()
        for item in items or []:
            out.add(_entry_match_key(str(item.get(key, ''))))
        return out

    orig_projects = { _entry_match_key(p.get('name', '')): p for p in original_resume_json.get('projects', []) }
    gen_projects = { _entry_match_key(p.get('name', '')): p for p in generated_resume_json.get('projects', []) }
    if not set(gen_projects).issubset(set(orig_projects)):
        raise SystemExit('Generated resume invalid: contains projects not present in original resume.json.')

    orig_exp = names(original_resume_json.get('experience', []), 'title')
    gen_exp = names(generated_resume_json.get('experience', []), 'title')
    if not gen_exp.issubset(orig_exp):
        raise SystemExit('Generated resume invalid: contains experience entries not present in original resume.json.')

    orig_edu = names(original_resume_json.get('education', []), 'degree')
    gen_edu = names(generated_resume_json.get('education', []), 'degree')
    if not gen_edu.issubset(orig_edu):
        raise SystemExit('Generated resume invalid: contains education entries not present in original resume.json.')

    orig_certs = {_normalize_term(c) for c in original_resume_json.get('certifications', [])}
    gen_certs = {_normalize_term(c) for c in generated_resume_json.get('certifications', [])}
    if not gen_certs.issubset(orig_certs):
        raise SystemExit('Generated resume invalid: contains certifications not present in original resume.json.')

    skills_allowlist = set()
    for values in (original_resume_json.get('skills', {}) or {}).values():
        for skill in values or []:
            s = _normalize_term(str(skill))
            if not s:
                continue
            skills_allowlist.add(s)
            skills_allowlist.add(s.replace(' ', ''))

    for key, project in gen_projects.items():
        orig_project = orig_projects[key]
        allowed_terms = _project_tech_allowlist_from_resume_json(orig_project) | skills_allowlist
        orig_bullets = bullet_texts(orig_project.get('bullets', []) or [])
        gen_bullets = [normalize_text(str(b)).strip() for b in (project.get('bullets', []) or []) if normalize_text(str(b)).strip()]
        if len(gen_bullets) > len(orig_bullets):
            raise SystemExit(f'Generated resume invalid: project "{project.get("name", "")}" has new bullets.')
        tech_terms = _extract_tech_terms(' '.join(gen_bullets))
        disallowed = [t for t in tech_terms if t not in allowed_terms]
        if disallowed:
            raise SystemExit(f'Generated resume invalid: project "{project.get("name", "")}" includes non-allowed tech terms.')
        orig_terms = _extract_tech_terms(' '.join(orig_bullets))
        orig_providers = _cloud_providers_in_terms(orig_terms | allowed_terms)
        gen_providers = _cloud_providers_in_terms(tech_terms)
        substituted = [p for p in gen_providers if p not in orig_providers]
        if substituted:
            raise SystemExit(f'Generated resume invalid: provider substitution in project "{project.get("name", "")}".')


def _apply_bullet_updates(entries: list[dict], updates: list[dict]) -> None:
    if not entries or not updates:
        return
    update_map = {}
    for item in updates:
        if not isinstance(item, dict):
            continue
        key = _entry_match_key(str(item.get('title', '')))
        bullets = item.get('bullets', [])
        if not key or not isinstance(bullets, list):
            continue
        cleaned = []
        seen = set()
        for b in bullets:
            txt = normalize_text(str(b)).strip()
            if not txt:
                continue
            k = txt.lower()
            if k in seen:
                continue
            seen.add(k)
            cleaned.append(txt)
        if cleaned:
            update_map[key] = cleaned[:6]
    for entry in entries:
        key = _entry_match_key(entry.get('title', ''))
        if key in update_map:
            entry['bullets'] = update_map[key][:]


def tailor_resume_selective_with_ai(
    job_text: str,
    resume_text: str,
    summary: str,
    skills: list[str],
    experience_entries: list[dict],
    project_entries: list[dict],
    classification: dict,
    strategy: dict,
    project_allowed_terms_by_title: dict[str, set[str]] | None = None,
) -> dict:
    if not _get_ai_provider():
        return {}
    structured_project_blocks = []
    for e in project_entries:
        title = e.get('title', '')
        key = _entry_match_key(title)
        structured_project_blocks.append({
            'title': title,
            'subtitle': e.get('subtitle', ''),
            'source_bullets': [
                {'id': idx + 1, 'text': b} for idx, b in enumerate(e.get('bullets', []) or [])
            ],
            'allowed_project_terms': sorted(list((project_allowed_terms_by_title or {}).get(key, set()))),
        })

    payload = {
        'classification': classification,
        'strategy': strategy,
        'summary': summary,
        'skills': skills,
        'experience_roles': [
            {'title': e.get('title', ''), 'subtitle': e.get('subtitle', ''), 'bullets': e.get('bullets', [])}
            for e in experience_entries
        ],
        'project_blocks': structured_project_blocks,
    }
    original_project_keys = {_entry_match_key(e.get('title', '')) for e in project_entries}
    original_project_entries_by_key = {_entry_match_key(e.get('title', '')): dict(e) for e in project_entries}
    last_data = {}

    for attempt in range(2):
        strict_warning = ''
        if attempt == 1:
            strict_warning = (
                "CRITICAL: Previous output violated project-scoped validation. "
                "For each project, do not use any technology term unless that exact project already contains it in the provided constraints.\n"
            )
        instructions = (
            "You are tailoring resume content for a specific job. Return STRICT JSON only.\n"
            "Do not add or remove roles. Update ONLY:\n"
            "1) professional summary text\n"
            "2) skill tags\n"
            "3) bullet points for provided project/work roles\n"
            "Rules:\n"
            "- No hallucinations; use only existing resume facts.\n"
            "- Keep summary concise (45-65 words).\n"
            "- Skills must come only from provided skill list.\n"
            "- For projects, you must only use and lightly rephrase the provided bullet points. Do not invent.\n"
            "- You may remove irrelevant bullets.\n"
            "- You may reorder bullet points to emphasize strategy priorities.\n"
            "- You may choose which projects to include and project order, but only from provided project blocks.\n"
            "- You may NOT add new skills.\n"
            "- You may NOT add new roles.\n"
            "- You may NOT create new bullet points.\n"
            "- You may NOT introduce new technologies or replace one technology with another.\n"
            "- You may NOT add metrics or new claims.\n"
            "- Keep each role title unchanged.\n"
            "- Keep 3-6 bullets per role when possible.\n"
            "- Project updates must obey allowed_project_terms for that same project.\n"
            "- Do not substitute cloud providers (example: Firebase cannot become AWS unless that project already used AWS).\n"
            "Output JSON shape:\n"
            "{"
            "\"summary\": string, "
            "\"skills\": [string], "
            "\"experience_updates\": [{\"title\": string, \"bullets\": [string]}], "
            "\"project_updates\": [{\"title\": string, \"bullets\": [string]}], "
            "\"selected_projects\": [string], "
            "\"project_order\": [string]"
            "}\n"
            f"{strict_warning}"
        )
        text = _call_ai_text(
            prompt=(
                f"Job description:\n{job_text}\n\n"
                f"Current resume content JSON:\n{json.dumps(payload, ensure_ascii=False)}\n"
            ),
            instructions=instructions,
            max_tokens=2200,
        )
        match = re.search(r'\{[\s\S]*\}', text or '')
        raw = match.group(0) if match else (text or '{}')
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        last_data = data
        ok, cleaned_project_updates, _reason = _validate_project_updates_section_scoped(
            data.get('project_updates', []),
            project_allowed_terms_by_title=project_allowed_terms_by_title or {},
            original_project_entries_by_key=original_project_entries_by_key,
            original_project_keys=original_project_keys,
        )
        if ok:
            data['project_updates'] = cleaned_project_updates
            return data

    if not isinstance(last_data, dict):
        return {}
    last_data['project_updates'] = []
    return last_data


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
            'certificates',
            'interests',
            'additional information',
        ]

    def section_sort_key(s):
        title = s['title'].lower()
        if title in preferred_order:
            return (0, preferred_order.index(title))
        return (1, title)

    for section in sorted(sections, key=section_sort_key):
        section_class = 'section section-projects' if section['title'].lower() == 'projects' else 'section'
        html_parts.append(f'<div class="{section_class}">')
        html_parts.append(f'<div class="section-title">{section["title"]}</div>')

        if section['skills']:
            html_parts.append('<div class="skills-grid">')
            for skill in section['skills']:
                html_parts.append(f'<span class="skill-tag">{skill}</span>')
            html_parts.append('</div>')

        for p in section['paragraphs']:
            html_parts.append(f'<p class="summary">{linkify_text(p)}</p>')

        for entry in section['entries']:
            html_parts.append('<div class="entry">')
            html_parts.append('<div class="entry-header">')
            html_parts.append(f'<span class="entry-title">{linkify_text_compact_links(entry.get("title", ""))}</span>')
            if entry.get('date'):
                html_parts.append(f'<span class="entry-date">{entry["date"]}</span>')
            html_parts.append('</div>')
            if entry.get('subtitle'):
                html_parts.append(f'<div class="entry-subtitle">{linkify_text_compact_links(entry["subtitle"])}</div>')
            if entry.get('bullets'):
                html_parts.append('<ul>')
                for b in entry['bullets']:
                    html_parts.append(f'<li>{linkify_text_compact_links(b)}</li>')
                html_parts.append('</ul>')
            html_parts.append('</div>')

        if section['bullets']:
            html_parts.append('<ul>')
            for b in section['bullets']:
                html_parts.append(f'<li>{linkify_text_compact_links(b)}</li>')
            html_parts.append('</ul>')

        html_parts.append('</div>')

    return '\n'.join(html_parts)


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
        if re.fullmatch(r'#+', line):
            continue

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

        if line in ('---', 'â€”', '--'):
            continue

        if line.startswith('- ') or line.startswith('* '):
            ensure_section()
            item = line[2:].strip()
            if current['title'].lower().find('skill') >= 0:
                # Split skills by commas and ignore category labels.
                if ':' in item:
                    item = item.split(':', 1)[1].strip()
                for part in _split_skills_csv(item):
                    cleaned = clean_skill_token(part)
                    if cleaned:
                        current['skills'].append(cleaned)
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
            'certificates',
            'interests',
            'additional information',
        ]

    def section_sort_key(s):
        title = s['title'].lower()
        if title in preferred_order:
            return (0, preferred_order.index(title))
        return (1, title)

    for section in sorted(sections, key=section_sort_key):
        section_class = 'section section-projects' if section['title'].lower() == 'projects' else 'section'
        html_parts.append(f'<div class="{section_class}">')
        html_parts.append(f'<div class="section-title">{section["title"]}</div>')

        if section['skills']:
            html_parts.append('<div class="skills-grid">')
            for skill in section['skills']:
                html_parts.append(f'<span class="skill-tag">{skill}</span>')
            html_parts.append('</div>')

        for p in section['paragraphs']:
            html_parts.append(f'<p class="summary">{linkify_text(p)}</p>')

        for entry in section['entries']:
            html_parts.append('<div class="entry">')
            html_parts.append('<div class="entry-header">')
            html_parts.append(f'<span class="entry-title">{linkify_text_compact_links(entry.get("title", ""))}</span>')
            if entry.get('date'):
                html_parts.append(f'<span class="entry-date">{entry["date"]}</span>')
            html_parts.append('</div>')
            if entry.get('subtitle'):
                html_parts.append(f'<div class="entry-subtitle">{linkify_text_compact_links(entry["subtitle"])}</div>')
            if entry.get('bullets'):
                html_parts.append('<ul>')
                for b in entry['bullets']:
                    html_parts.append(f'<li>{linkify_text_compact_links(b)}</li>')
                html_parts.append('</ul>')
            html_parts.append('</div>')

        if section['bullets']:
            html_parts.append('<ul>')
            for b in section['bullets']:
                html_parts.append(f'<li>{linkify_text_compact_links(b)}</li>')
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
    """Validate every meaningful word in the tagline appears in the resume.

    Dot-separated terms (ASP.NET, Node.js) are checked as complete strings to
    prevent 'net' in 'ASP.NET' matching against URLs in the resume text.
    Hyphenated compounds (Cybersecurity-Focused) are split and each part checked.
    Returns the tagline if valid, or None to trigger fallback.
    """
    if not tagline:
        return None
    if len(tagline.split()) > 20:
        return None
    resume_l = normalize_text(resume_text).lower()
    stop = {
        'and', 'or', 'for', 'with', 'in', 'on', 'to', 'of', 'the', 'a', 'an',
        'developer', 'engineer', 'analyst', 'specialist',
        # Generic compound-word role modifiers that need not appear verbatim
        'full', 'stack', 'focused', 'based', 'driven', 'oriented', 'led',
    }
    for segment in tagline.split('|'):
        seg = segment.strip()
        if not seg:
            continue
        for word in seg.split():
            w = word.strip('!?,;:\'"[](){}Â·')
            if not w:
                continue
            w_l = w.lower()
            # Dot-separated terms must match as a COMPLETE string (prevents
            # 'net' in 'ASP.NET' matching against URLs in the resume).
            if '.' in w_l:
                if w_l not in resume_l:
                    return None
                continue
            # Hyphenated compounds: check each part independently so
            # 'Cybersecurity-Focused' checks 'cybersecurity' not the compound.
            parts = w_l.split('-') if '-' in w_l else [w_l]
            for part in parts:
                if part in stop or len(part) < 3:
                    continue
                if part not in resume_l:
                    return None
    return tagline


def generate_tagline_with_ai(job_text: str, resume_text: str) -> str | None:
    if not _get_ai_provider():
        return None
    short_resume = _truncate(resume_text, 1500)
    short_job = _truncate(job_text, 1000)
    prompt = (
        "Create a role-specific resume tagline based on the job description and the resume. "
        "Return a single line only, no quotes, no extra text. "
        "Format: Role | Skill | Skill | Skill (pipe-separated, 3-4 segments). "
        "STRICT RULE: Use only roles/skills/terms that already appear in the resume text provided. "
        "Do NOT invent or add new tools, skills, technologies, or roles.\n\n"
        f"Job description:\n{short_job}\n\nResume:\n{short_resume}\n"
    )
    try:
        text = _call_ai_text(prompt=prompt, max_tokens=64, model_tier='fast')
    except Exception:
        return None
    if text:
        tagline = text.strip().splitlines()[0]
        return _validate_tagline(tagline, resume_text)
    return None


def generate_tagline_with_claude(job_text: str, resume_text: str) -> str | None:
    return generate_tagline_with_ai(job_text=job_text, resume_text=resume_text)


def generate_cover_letter_with_ai(job_text: str, resume_text: str, name: str) -> str:
    if not _get_ai_provider():
        raise SystemExit('OPENAI_API_KEY or ANTHROPIC_API_KEY is required to use AI cover letters.')
    prompt = (
        "You are a professional cover letter writer and recruitment specialist.\n\n"
        "I will provide you with:\n\n"
        "A job description\n\n"
        "My resume\n\n"
        "Your task:\n\n"
        "Write a highly tailored cover letter for this specific role.\n\n"
        "Strict rules (must follow):\n\n"
        "DO NOT invent or exaggerate experience, achievements, or skills.\n\n"
        "DO NOT add fake metrics, fake projects, or fake responsibilities.\n\n"
        "Only use information that already exists in my resume.\n\n"
        "Never add technologies, tools, or outcomes that are not already present in my resume.\n\n"
        "If something is not in my resume, do not mention it.\n\n"
        "You may reword and present my experience in a stronger way, but the meaning must stay truthful.\n\n"
        "Use keywords and language from the job description where relevant, but only when it matches my actual experience.\n\n"
        "Cover letter requirements:\n\n"
        "Tone must be confident, professional, and modern (not generic or robotic).\n\n"
        "It must sound like a real person wrote it, not AI.\n\n"
        "Keep it concise: 300â€“450 words max.\n\n"
        "Structure it properly:\n\n"
        "Strong opening paragraph (role + excitement + value)\n\n"
        "Middle paragraph(s) linking my skills/projects to the job requirements\n\n"
        "Closing paragraph with enthusiasm + call to action\n\n"
        "Formatting rules:\n\n"
        "Use Australian/UK spelling.\n\n"
        "Do not use overly formal outdated wording (avoid: \"To whom it may concern\").\n\n"
        "Address the company by name. If the company name is not present in the job description, use: \"Dear Hiring Manager\".\n\n"
        f"End with:\nKind regards,\n\n{name}\n\n"
        "Return plain text only. Do not include a subject line.\n\n"
        f"Job description:\n{job_text}\n\nResume:\n{resume_text}\n"
    )
    text = _call_ai_text(prompt=prompt, max_tokens=1024, model_tier='smart')
    if text:
        return text.strip()
    raise SystemExit('AI response did not include text output.')


def generate_cover_letter_with_claude(job_text: str, resume_text: str, name: str) -> str:
    return generate_cover_letter_with_ai(job_text=job_text, resume_text=resume_text, name=name)


def _parse_cover_letter_paragraphs(cover_text: str) -> list[tuple[str, str]]:
    raw_lines = [line.rstrip() for line in cover_text.strip().splitlines()]
    lines = [line for line in raw_lines if line.strip() or line == '']

    paragraphs: list[str] = []
    buf: list[str] = []
    for line in lines:
        if not line.strip():
            if buf:
                paragraphs.append(' '.join(buf).strip())
                buf = []
            continue
        buf.append(line.strip())
    if buf:
        paragraphs.append(' '.join(buf).strip())

    styled: list[tuple[str, str]] = []
    for i, para in enumerate(paragraphs):
        lower = para.lower()
        if lower.startswith('kind regards'):
            styled.append(('signature', 'Kind regards,'))
            if i + 1 < len(paragraphs):
                styled.append(('signature', paragraphs[i + 1]))
            break
        styled.append(('body', para))
    return styled


def _build_cover_letter_html(style_css: str, header_html: str, cover_text: str) -> str:
    paragraphs = _parse_cover_letter_paragraphs(cover_text)
    blocks = []
    for cls, text in paragraphs:
        class_attr = 'signature' if cls == 'signature' else 'body'
        blocks.append(f'<p class="{class_attr}">{text}</p>')
    body = "\n".join(blocks)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cover Letter</title>
<style>
{style_css}
.section-title {{ font-weight: 700; margin-top: 16px; }}
.cover-letter p {{ margin: 0 0 10px; }}
.cover-letter .signature {{ margin-top: 10px; }}
@media print {{
  .page {{ padding-top: 6mm; }}
}}
@media screen {{
  .page {{ padding-top: 24px; }}
}}
</style>
</head>
<body>
<div class="page">
{header_html}
<div class="section">
  <div class="section-title">Cover Letter</div>
  <div class="cover-letter">
    {body}
  </div>
</div>
</div>
</body>
</html>
"""


def generate_cover_letter(
    resume_path,
    job_text: str,
    out_dir,
    label: str | None = None,
    template_path=None,
    tagline: str | None = None,
):
    resume_path = Path(resume_path)
    if not resume_path.exists():
        raise SystemExit(f'Resume file not found: {resume_path}')
    resume_json = load_resume_json(resume_path)
    resume_text = resume_json_to_text(resume_json)
    name = normalize_text((resume_json.get('basics', {}) or {}).get('name', '')).strip()

    cover_text = generate_cover_letter_with_ai(
        job_text=job_text,
        resume_text=resume_text,
        name=name or 'Candidate',
    )

    template_header_html = None
    style_css = ''
    if template_path:
        template_path = Path(template_path)
        if not template_path.exists():
            raise SystemExit(f'Template file not found: {template_path}')
        template_text = template_path.read_text(encoding='utf-8', errors='replace')
        style_match = re.search(r'<style>(.*?)</style>', template_text, re.DOTALL | re.IGNORECASE)
        if style_match:
            style_css = style_match.group(1).strip()
        template_header_html = extract_template_header(template_text)

    header_html = template_header_html or render_header_html(name=name, headline='', contact='')
    if (job_text or '').strip():
        cover_tagline = tagline or generate_tagline_with_ai(job_text=job_text, resume_text=resume_text)
        if cover_tagline:
            header_html = _apply_tagline_to_header(header_html, cover_tagline)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_label = re.sub(r'[^A-Za-z0-9_-]+', '-', (label or 'Tailored')).strip('-') or 'Tailored'
    base = f'CoverLetter_{safe_label}_{stamp}'
    txt_path = out_dir / f'{base}.txt'
    html_path = out_dir / f'{base}.html'
    pdf_path = out_dir / f'{base}.pdf'
    txt_path.write_text(cover_text, encoding='utf-8')

    if style_css:
        html = _build_cover_letter_html(style_css=style_css, header_html=header_html, cover_text=cover_text)
        html_path.write_text(html, encoding='utf-8')

        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            raise SystemExit('Playwright is required. Install with: python -m pip install playwright && python -m playwright install chromium') from e

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until='networkidle')
            page.pdf(path=str(pdf_path), format='A4', print_background=True, margin={
                'top': '0', 'right': '0', 'bottom': '0', 'left': '0'
            })
            browser.close()

    return html_path if html_path.exists() else txt_path, pdf_path if pdf_path.exists() else None, cover_text


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
        'projects',
        'professional experience',
        'education',
        'key skills / technical skills',
        'key skills',
        'technical skills',
        'professional summary',
    ]
    bullet_required_sections = {
        'projects',
        'professional experience',
        'work experience/projects',
        'volunteer experience',
    }

    def find_section(title):
        for s in sections:
            if s['title'].lower() == title:
                return s
        return None

    def is_protected_entry(entry) -> bool:
        return 'bunkerify' in normalize_text(entry.get('title', '')).lower()

    for title in priority:
        section = find_section(title)
        if not section:
            continue
        # Trim entries' bullets
        if section['entries']:
            for e in reversed(section['entries']):
                if is_protected_entry(e):
                    continue
                if e.get('bullets'):
                    e['bullets'].pop()
                    # In experience/project style sections, hide entries with no detail bullets.
                    if (
                        section['title'].lower() in bullet_required_sections
                        and not e.get('bullets')
                    ):
                        section['entries'].remove(e)
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


def tailor_resume_with_ai(
    job_text: str,
    resume_text: str,
    allowed_sections: list[str] | None = None,
    fallback_tagline: str | None = None,
):
    if not _get_ai_provider():
        raise SystemExit('OPENAI_API_KEY or ANTHROPIC_API_KEY is required to use AI tailoring.')

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
        "Return the updated resume in a clean professional format using this exact order:\n\n"
        "Professional Summary\n\n"
        "Key Skills / Technical Skills\n\n"
        "Professional Experience\n\n"
        "Projects\n\n"
        "Education\n\n"
        "Certifications\n\n"
        "Additional Information\n\n"
        "Content requirements:\n\n"
        "TAGLINE should be role-specific in this style: Cybersecurity-Focused Software Engineer | Full-Stack Development | ACSC Essential Eight | Production Systems.\n\n"
        "Professional Summary must lead with Bunkerify as proof of production impact and mention current Master of Computer Science (Cybersecurity) studies at UOW.\n\n"
        "Professional Summary must be written in first person without using the candidate's name. "
        "Do not start with the candidate's name. Do not use third-person phrasing.\n\n"
        "Key Skills / Technical Skills should be tailored to the job and include only relevant skills from my resume.\n\n"
        "Group all AWS services under a single tag formatted as 'AWS (EC2, S3, IAM, CloudWatch)'. "
        "Do not emit EC2, S3, RDS, IAM, CloudWatch, CloudTrail, or any individual AWS service as a separate skill tag.\n\n"
        "Professional Experience must include only paid/volunteer tech roles: Catch a Drive and Hentley.\n\n"
        "Projects must include Bunkerify first, then Production Support Incident Console, Job Application Assistant, and Cancer Awareness Mobile App.\n\n"
        "Education should list Bachelor before Master. If Master courses are empty, remove the empty field.\n\n"
        "Certifications should include AWS Academy Cloud Foundations.\n\n"
        "Place driving roles only under Additional Information.\n\n"
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

    text = _call_ai_text(
        prompt=(
            "Job description:\n"
            f"{job_text}\n\n"
            "Current resume:\n"
            f"{resume_text}\n"
        ),
        instructions=instructions,
        max_tokens=3072,
        model_tier='smart',
    )
    if text:
        text = text.strip()
        tagline, body = _extract_tagline(text)
        if not tagline:
            tagline = generate_tagline_with_ai(job_text=job_text, resume_text=resume_text) or fallback_tagline
        return tagline, body
    raise SystemExit('AI response did not include text output.')


def tailor_resume_with_claude(
    job_text: str,
    resume_text: str,
    allowed_sections: list[str] | None = None,
    fallback_tagline: str | None = None,
):
    return tailor_resume_with_ai(
        job_text=job_text,
        resume_text=resume_text,
        allowed_sections=allowed_sections,
        fallback_tagline=fallback_tagline,
    )


def generate_resume(resume_path, template_path, job_text=None, out_dir=None, label=None, job_label=None):
    resume_path = Path(resume_path)
    template_path = Path(template_path)
    if not resume_path.exists():
        raise SystemExit(f'Resume file not found: {resume_path}')
    if not template_path.exists():
        raise SystemExit(f'Template file not found: {template_path}')

    resume_json = load_resume_json(resume_path)
    resume_text = resume_json_to_text(resume_json)
    parsed_resume = resume_json_to_internal(resume_json)
    template_text = template_path.read_text(encoding='utf-8', errors='replace')
    style_match = re.search(r'<style>(.*?)</style>', template_text, re.DOTALL | re.IGNORECASE)
    if style_match:
        style_css = style_match.group(1).strip()
    else:
        style_css = ''
    extra_pdf_css = "\n@media print { .page { padding-top: 6mm; } }\n"
    style_css = style_css + extra_pdf_css

    name = parsed_resume.get('name', '')
    contact = [c for c in (parsed_resume.get('contact', []) or []) if c]

    raw_job_text = job_text or ''
    classification = classify_job(raw_job_text)
    strategy = choose_resume_strategy(classification=classification, job_text=raw_job_text)
    tagline = strategy.get('tagline') or resume_json.get('headline') or DEFAULT_TAILORED_TAGLINE
    headline = tagline

    summary = build_summary(classification=classification, resume_json=resume_json, job_text=raw_job_text)

    education = parsed_resume.get('education', [])
    experience = [dict(e) for e in (parsed_resume.get('experience', []) or [])]
    volunteer_entries = []
    projects = [dict(p) for p in (parsed_resume.get('projects', {}) or {}).values()]
    projects = filter_projects_for_role(
        projects,
        role_profile=str(strategy.get('role_profile', 'general')),
        job_text=raw_job_text,
    )
    projects = reorder_projects_by_priority(projects, strategy.get('project_priority', []))
    projects = select_project_bullets_deterministic(
        projects=projects,
        job_text=raw_job_text,
        max_bullets_per_project=int(strategy.get('max_bullets_per_project', 3)),
        min_bullets_per_project=1,
        role_category=str(classification.get('primary_category', 'unknown')),
    )
    projects = [p for p in projects if p.get('bullets')]
    experience = select_experience_bullets_for_render(
        entries=experience,
        min_bullets=2,
        max_bullets=2,
    )

    grouped_skills = parsed_resume.get('skills_grouped', {})
    ordered_grouped_skills, _ordered_skills = reorder_skill_groups(
        grouped_skills=grouped_skills,
        priority_groups=SKILL_GROUP_KEYS,
    )
    skills = select_skills_deterministic(
        job_text=raw_job_text,
        grouped_skills=ordered_grouped_skills,
        max_skills=int(strategy.get('max_skills', 18)),
        role_category=str(classification.get('primary_category', 'unknown')),
    )

    certificates = parsed_resume.get('certificates', [])
    interests = parsed_resume.get('interests', [])
    header_html = render_header_html(name=name, headline=headline, contact=contact)

    deterministic_projects = [dict(p) for p in projects]
    deterministic_summary = summary

    if _get_ai_provider() and raw_job_text.strip():
        payload = light_rephrase_selected_content_with_ai(
            job_text=raw_job_text,
            summary=summary,
            selected_projects=projects,
        )
        if payload:
            by_name = {_entry_match_key(p.get('title', '')): dict(p) for p in projects}
            for row in payload.get('projects', []):
                key = _entry_match_key(row.get('name', ''))
                if key not in by_name:
                    continue
                source_bullets = projects[[_entry_match_key(p.get('title', '')) for p in projects].index(key)].get('bullets', [])
                rewritten = [normalize_text(str(b)).strip() for b in source_bullets]
                for bullet in row.get('bullets', []):
                    idx = bullet.get('source_index')
                    text = normalize_text(str(bullet.get('text', ''))).strip()
                    if isinstance(idx, int) and 0 <= idx < len(source_bullets) and text:
                        rewritten[idx] = text
                by_name[key]['bullets'] = rewritten
            ai_projects = [by_name[_entry_match_key(p.get('title', ''))] for p in projects if _entry_match_key(p.get('title', '')) in by_name]
            candidate_generated_json = {
                'basics': resume_json.get('basics', {}),
                'headline': headline,
                'summary': deterministic_summary,
                'education': resume_json.get('education', []),
                'projects': [
                    {
                        'name': p.get('title', ''),
                        'links': next(
                            (proj.get('links', {}) for proj in resume_json.get('projects', []) if _entry_match_key(proj.get('name', '')) == _entry_match_key(p.get('title', ''))),
                            {},
                        ),
                        'technologies': next(
                            (proj.get('technologies', []) for proj in resume_json.get('projects', []) if _entry_match_key(proj.get('name', '')) == _entry_match_key(p.get('title', ''))),
                            [],
                        ),
                        'bullets': p.get('bullets', []),
                    }
                    for p in ai_projects
                ],
                'experience': resume_json.get('experience', []),
                'skills': ordered_grouped_skills,
                'certifications': resume_json.get('certifications', []),
            }
            try:
                validate_generated_resume(original_resume_json=resume_json, generated_resume_json=candidate_generated_json)
                summary = deterministic_summary
                projects = ai_projects
            except (Exception, SystemExit):
                summary = deterministic_summary
                projects = [dict(p) for p in deterministic_projects]

    deterministic_generated_json = {
        'basics': resume_json.get('basics', {}),
        'headline': headline,
        'summary': summary,
        'education': resume_json.get('education', []),
        'projects': [
            {
                'name': p.get('title', ''),
                'links': next(
                    (proj.get('links', {}) for proj in resume_json.get('projects', []) if _entry_match_key(proj.get('name', '')) == _entry_match_key(p.get('title', ''))),
                    {},
                ),
                'technologies': next(
                    (proj.get('technologies', []) for proj in resume_json.get('projects', []) if _entry_match_key(proj.get('name', '')) == _entry_match_key(p.get('title', ''))),
                    [],
                ),
                'bullets': p.get('bullets', []),
            }
            for p in projects
        ],
        'experience': resume_json.get('experience', []),
        'skills': ordered_grouped_skills,
        'certifications': resume_json.get('certifications', []),
    }
    validate_generated_resume(original_resume_json=resume_json, generated_resume_json=deterministic_generated_json)

    keywords = extract_keywords(raw_job_text, skills)
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
        section_order=strategy.get('section_order', DEFAULT_SECTION_ORDER),
        grouped_skills=ordered_grouped_skills,
        skill_priority_groups=strategy.get('skill_priority_groups', []),
    )

    out_dir = Path(out_dir) if out_dir else (Path(tempfile.gettempdir()) / 'job-application-assistant')
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

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until='networkidle')
        page.pdf(path=str(pdf_path), format='A4', print_background=True, margin={
            'top': '0', 'right': '0', 'bottom': '0', 'left': '0'
        })
        browser.close()

    return html_path, pdf_path, tagline



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

    html_path, pdf_path, _tagline = generate_resume(
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


