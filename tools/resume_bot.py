import argparse
import html
import hashlib
import json
import os
import re
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

DASH_LINE = re.compile(r'^-\s*-\s*-\s*[-\s]*$')
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
    'Interests',
    'Additional Information',
]
SUMMARY_BANNED_RE = re.compile(r"\b(c#|c\s*sharp|\.net|asp\.?net|dotnet)\b", re.IGNORECASE)


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


def parse_resume_sections(resume_text: str) -> dict:
    _, source_sections = split_sections(resume_text)
    summary_lines = source_sections.get('Software Engineer', [])
    summary = ' '.join([l for l in summary_lines if l.strip()])

    work_entries = parse_experience(source_sections.get('Work experience/Projects', []))
    work_entries = [e for e in work_entries if not _is_excluded_project_title(e.get('title', ''))]
    volunteer_entries = parse_experience(source_sections.get('Volunteer Experience', []))

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
        'skills': parse_skills(source_sections.get('Skills', [])),
        'certificates': parse_list(source_sections.get('Certificates', [])),
        'experience': experience + volunteer_entries,
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
    secondary_value = data.get('secondary_category')
    secondary = normalize_text(str(secondary_value)).strip().lower() if secondary_value is not None else None
    if not secondary:
        secondary = None
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
        'secondary_category': secondary,
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
        "\"secondary_category\": string|null,"
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
            'application development', 'agile',
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
    secondary = None
    if best_score > 0:
        primary = best_cat
        if second_score > 0:
            secondary = second_cat
    if primary == 'software_engineering' and scores.get('cybersecurity', 0) >= best_score:
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
        'secondary_category': secondary,
        'top_keywords': top_keywords,
        'tone': tone,
        'confidence': confidence,
    })


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
    _JOB_CLASSIFICATION_CACHE[key] = dict(result)
    return result


def choose_resume_strategy(classification: dict) -> dict:
    category = str((classification or {}).get('primary_category', 'unknown')).lower()
    is_cyber = category == 'cybersecurity'
    if is_cyber:
        return {
            'name': 'CYBERSECURITY',
            'tagline': (
                'Cybersecurity-Focused Software Engineer | Full-Stack Development | '
                'ACSC Essential Eight | Incident Response'
            ),
            'section_order': [
                'Professional Summary',
                'Key Skills / Technical Skills',
                'Projects',
                'Professional Experience',
                'Education',
                'Certifications',
                'Interests',
                'Additional Information',
            ],
            'project_priority': [
                'Bunkerify',
                'Production Support Incident Console',
                'Job Application Assistant',
                'Cancer Awareness Mobile App',
            ],
            'max_skills': 16,
            'min_skills': 10,
            'prefer_cyber_terms': True,
        }

    return {
        'name': 'SOFTWARE_GENERAL',
        'tagline': (
            'Cybersecurity-Focused Software Engineer | Full-Stack Development | '
            'Production Systems | ACSC Essential Eight'
        ),
        'section_order': DEFAULT_SECTION_ORDER[:],
        'project_priority': [
            'Job Application Assistant',
            'Production Support Incident Console',
            'Bunkerify',
            'Cancer Awareness Mobile App',
        ],
        'max_skills': 16,
        'min_skills': 10,
        'prefer_cyber_terms': False,
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


def _normalize_term(value: str) -> str:
    text = normalize_text(value or '').strip().lower()
    text = re.sub(r'\s+', ' ', text)
    return text


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
            label = '🐙 Github'
        elif 'linkedin.com' in low:
            label = '💼 LinkedIn'
        elif 'mobeenkhan.com' in low:
            label = '🌐 Portfolio'
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    if '@' in raw and ' ' not in raw:
        return f'📧 {html.escape(raw)}'

    if re.search(r'^\+?[0-9][0-9\s\-]{6,}$', raw):
        return f'📞 {html.escape(raw)}'

    if 'sydney' in low:
        return f'📍 {html.escape(raw)}'

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
):
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

    def render_entries(entries, with_subtitle=False):
        html = []
        for e in entries:
            html.append('<div class="entry">')
            html.append('<div class="entry-header">')
            html.append(f'<span class="entry-title">{linkify_text_compact_links(e.get("title", ""))}</span>')
            if e.get('date'):
                html.append(f'<span class="entry-date">{e["date"]}</span>')
            html.append('</div>')
            if with_subtitle and e.get('school'):
                html.append(f'<div class="entry-subtitle">{linkify_text(e["school"])}</div>')
            elif e.get('subtitle'):
                html.append(f'<div class="entry-subtitle">{linkify_text_compact_links(e["subtitle"])}</div>')
            if e.get('bullets'):
                html.append('<ul>')
                for b in e['bullets']:
                    html.append(f'<li>{linkify_text(b)}</li>')
                html.append('</ul>')
            html.append('</div>')
        return '\n'.join(html)

    edu_html = render_entries(education, with_subtitle=True)
    skills_html = ''.join([f'<span class="skill-tag">{s}</span>' for s in skills])
    proj_html = render_entries(projects)
    combined_experience = (experience or []) + (volunteer or [])
    professional_entries = []
    additional_entries = []
    for e in combined_experience:
        title_l = normalize_text(e.get('title', '')).lower()
        if _is_driving_role(e) or 'independent contractor' in title_l:
            add_entry = dict(e)
            add_entry['bullets'] = []
            additional_entries.append(add_entry)
        else:
            professional_entries.append(e)
    exp_html = render_entries(professional_entries)
    cert_items = ''.join([f'<li>{linkify_text(c)}</li>' for c in certificates]) if certificates else ''
    interests_text = linkify_text(' - '.join(interests)) if interests else ''

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
        'Interests': (
            f'<div class="section"><div class="section-title">Interests</div><p class="summary">{interests_text}</p></div>'
            if interests_text else ''
        ),
        'Additional Information': (
            '<div class="section"><div class="section-title">Additional Information</div>'
            f'{render_entries(additional_entries)}</div>' if additional_entries else ''
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
{''.join(body_sections)}

</div>
</body>
</html>
"""
    return html


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

        if line in ('---', '—', '--'):
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
    # (rank=3) must always appear — force-inject from fallback if still missing.
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
    'jest', 'react testing library', 'axios', 'vite', 'sendgrid', 'calendly',
    'aws', 'amazon web services', 'azure', 'gcp', 'google cloud', 'asp.net', '.net', 'dotnet', 'c#',
]

_CLOUD_PROVIDER_VARIANTS = {
    'aws': {'aws', 'amazon web services'},
    'azure': {'azure', 'microsoft azure'},
    'gcp': {'gcp', 'google cloud', 'google cloud platform'},
    'firebase': {'firebase', 'cloud firestore'},
}


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
        token_n = _normalize_term(token)
        if not token_n:
            continue
        techy = (
            any(ch in token_n for ch in ('+', '#', '.', '/')) or
            token_n in {'api', 'sql', 'nosql', 'firebase', 'react', 'typescript', 'javascript', 'aws', 'azure', 'gcp'}
        )
        if techy:
            found.add(token_n)
    return found


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
        generated_terms = _extract_tech_terms(' '.join(normalized_bullets))
        allowed_terms = project_allowed_terms_by_title.get(key, set())
        disallowed = sorted(t for t in generated_terms if t not in allowed_terms)
        if disallowed:
            return False, [], f'project tech terms not in project allowlist for {title}: {", ".join(disallowed[:5])}'

        original_providers = _cloud_providers_in_terms(allowed_terms)
        generated_providers = _cloud_providers_in_terms(generated_terms)
        substituted = sorted(p for p in generated_providers if p not in original_providers)
        if substituted:
            return False, [], f'cloud provider substitution in {title}: {", ".join(substituted)}'

        cleaned_updates.append({'title': title, 'bullets': normalized_bullets[:6]})
    return True, cleaned_updates, ''


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
    payload = {
        'classification': classification,
        'strategy': strategy,
        'summary': summary,
        'skills': skills,
        'experience_roles': [
            {'title': e.get('title', ''), 'subtitle': e.get('subtitle', ''), 'bullets': e.get('bullets', [])}
            for e in experience_entries
        ],
        'project_roles': [
            {'title': e.get('title', ''), 'subtitle': e.get('subtitle', ''), 'bullets': e.get('bullets', [])}
            for e in project_entries
        ],
        'project_term_constraints': {
            entry.get('title', ''): sorted(list(project_allowed_terms_by_title.get(_entry_match_key(entry.get('title', '')), set())))
            for entry in project_entries
        } if project_allowed_terms_by_title else {},
    }
    original_project_keys = {_entry_match_key(e.get('title', '')) for e in project_entries}
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
            "- You may reorder bullet points to emphasize strategy priorities.\n"
            "- You may NOT add new skills.\n"
            "- You may NOT add new roles.\n"
            "- Keep each role title unchanged.\n"
            "- Keep 3-6 bullets per role when possible.\n"
            "- Project updates must obey project_term_constraints for that same project.\n"
            "- Do not substitute cloud providers (example: Firebase cannot become AWS unless that project already used AWS).\n"
            "Output JSON shape:\n"
            "{"
            "\"summary\": string, "
            "\"skills\": [string], "
            "\"experience_updates\": [{\"title\": string, \"bullets\": [string]}], "
            "\"project_updates\": [{\"title\": string, \"bullets\": [string]}]"
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

        if line in ('---', '—', '--'):
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
            w = word.strip('!?,;:\'"[](){}·')
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
        "If something is not in my resume, do not mention it.\n\n"
        "You may reword and present my experience in a stronger way, but the meaning must stay truthful.\n\n"
        "Use keywords and language from the job description where relevant, but only when it matches my actual experience.\n\n"
        "Cover letter requirements:\n\n"
        "Tone must be confident, professional, and modern (not generic or robotic).\n\n"
        "It must sound like a real person wrote it, not AI.\n\n"
        "Keep it concise: 300–450 words max.\n\n"
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
    resume_text = resume_path.read_text(encoding='utf-8', errors='replace')
    header_lines, _sections = split_sections(resume_text)
    name, _contact = parse_header(header_lines)

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

    resume_text = resume_path.read_text(encoding='utf-8', errors='replace')
    template_text = template_path.read_text(encoding='utf-8', errors='replace')
    style_match = re.search(r'<style>(.*?)</style>', template_text, re.DOTALL | re.IGNORECASE)
    if style_match:
        style_css = style_match.group(1).strip()
    else:
        style_css = ''
    extra_pdf_css = "\n@media print { .page { padding-top: 6mm; } }\n"
    style_css = style_css + extra_pdf_css

    header_lines, source_sections = split_sections(resume_text)
    name, contact = parse_header(header_lines)

    classification = classify_job(job_text or '')
    strategy = choose_resume_strategy(classification)
    tagline = strategy.get('tagline') or DEFAULT_TAILORED_TAGLINE
    headline = tagline

    template_skill_tags = extract_template_skill_tags(template_text)

    parsed_resume = parse_resume_sections(resume_text)
    sections = source_sections
    summary = parsed_resume.get('summary', '')
    if not summary:
        summary = 'Software engineer with a strong foundation in web technologies, networking, and object-oriented programming.'
    summary_result = generate_summary_with_guard(
        job_text=job_text or '',
        resume_text=resume_text,
        fallback_summary=summary,
        classification=classification,
        strategy=strategy,
    )
    summary = _sanitize_summary_text(
        str(summary_result.get('summary', '')),
        fallback=summary,
        max_words=65,
    )

    education = parse_education(sections.get('Education', []))
    work_entries = parse_experience(sections.get('Work experience/Projects', []))
    work_entries = [e for e in work_entries if not _is_excluded_project_title(e.get('title', ''))]
    volunteer_entries = parse_experience(sections.get('Volunteer Experience', []))

    # Keep original role/project structure fixed; AI can only update bullet text.
    projects = []
    experience = []
    for e in work_entries:
        title_l = e['title'].lower()
        if 'independent contractor' in title_l or 'web developer' in title_l or 'driver' in title_l:
            experience.append(e)
        else:
            projects.append(e)
    projects = reorder_projects_by_priority(projects, strategy.get('project_priority', []))
    project_allowed_terms_by_title = build_project_allowed_terms(parsed_resume.get('projects', {}))

    canonical_skills = template_skill_tags[:] if template_skill_tags else parse_skills(sections.get('Skills', []))
    skills = filter_skills_for_job(
        canonical_skills,
        job_text=job_text or '',
        max_skills=int(strategy.get('max_skills', 16)),
        min_skills=int(strategy.get('min_skills', 10)),
        prefer_cyber_terms=bool(strategy.get('prefer_cyber_terms', False)),
    )

    certificates = parse_list(sections.get('Certificates', []))
    interests = parse_list(sections.get('Interests', []))
    header_html = render_header_html(name=name, headline=headline, contact=contact)

    if _get_ai_provider() and (job_text or '').strip():
        try:
            selective = tailor_resume_selective_with_ai(
                job_text=job_text,
                resume_text=resume_text,
                summary=summary,
                skills=canonical_skills,
                experience_entries=experience + volunteer_entries,
                project_entries=projects,
                classification=classification,
                strategy=strategy,
                project_allowed_terms_by_title=project_allowed_terms_by_title,
            )
        except Exception:
            selective = {}
        skills = _sanitize_skills_from_ai(
            selective.get('skills', []),
            canonical_skills=canonical_skills,
            job_text=job_text or '',
            max_skills=int(strategy.get('max_skills', 16)),
            min_skills=int(strategy.get('min_skills', 10)),
            prefer_cyber_terms=bool(strategy.get('prefer_cyber_terms', False)),
        )
        _apply_bullet_updates(experience, selective.get('experience_updates', []))
        _apply_bullet_updates(volunteer_entries, selective.get('experience_updates', []))
        _apply_bullet_updates(projects, selective.get('project_updates', []))
        projects = reorder_projects_by_priority(projects, strategy.get('project_priority', []))

    keywords = extract_keywords(job_text or '', skills)
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
