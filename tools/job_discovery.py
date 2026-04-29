"""Job discovery and ranking helpers.

This module intentionally avoids logging in to LinkedIn/SEEK/Indeed or scraping pages.
It is the safe foundation for an application copilot: ingest job posting text,
score it against the canonical resume JSON, and return a ranked shortlist that a
human can approve before generating documents or submitting applications.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from tools.resume_bot import classify_job_heuristic, normalize_text, resume_json_to_text


PLATFORM_HINTS = {
    'linkedin.com': 'LinkedIn',
    'seek.com.au': 'SEEK',
    'indeed.com': 'Indeed',
    'indeed.com.au': 'Indeed',
}

NEGATIVE_SIGNALS = [
    'senior',
    'lead engineer',
    'principal',
    'staff engineer',
    '10+ years',
    '8+ years',
    '7+ years',
    '6+ years',
    '5+ years',
    'must be eligible for security clearance',
]

POSITIVE_SIGNALS = [
    'graduate',
    'junior',
    'entry level',
    'entry-level',
    'associate',
    'software engineer',
    'full stack',
    'full-stack',
    'cybersecurity',
    'application security',
    'devsecops',
    'react',
    'typescript',
    'javascript',
    'python',
    'flask',
    'rest api',
    'supabase',
    'github actions',
    'ci/cd',
    'incident',
    'vulnerability',
    'essential eight',
]


@dataclass(frozen=True)
class JobPosting:
    title: str
    company: str
    location: str
    url: str
    platform: str
    description: str


def parse_job_postings(raw_text: str) -> list[JobPosting]:
    """Parse pasted job postings into rough structured records.

    Supported formats:
    - Multiple copied postings separated by a line containing `---` or `===`.
    - CSV with headers such as title, company, location, url, platform, description.

    The free-text parser is deliberately forgiving. The first non-empty line is
    treated as title unless it looks like a URL; URL/platform/company/location are
    inferred from obvious labels when present.
    """
    if _looks_like_csv(raw_text):
        csv_rows = _parse_csv_postings(raw_text)
        if csv_rows:
            return csv_rows
    blocks = [b.strip() for b in re.split(r'(?m)^\s*(?:---|===)\s*$', raw_text or '') if b.strip()]
    postings: list[JobPosting] = []
    for block in blocks:
        if _looks_like_csv(block):
            postings.extend(_parse_csv_postings(block))
        else:
            postings.append(_parse_one_block(block))
    return postings


def _looks_like_csv(raw_text: str) -> bool:
    first_line = next((line.strip().lower() for line in (raw_text or '').splitlines() if line.strip()), '')
    headers = {part.strip() for part in first_line.split(',')}
    return ',' in first_line and any(key in headers for key in ('title', 'role', 'job title', 'description', 'company'))


def _parse_csv_postings(raw_text: str) -> list[JobPosting]:
    rows: list[JobPosting] = []
    try:
        reader = csv.DictReader((raw_text or '').splitlines())
        for raw_row in reader:
            row = {str(k or '').strip().lower(): normalize_text(str(v or '')).strip() for k, v in raw_row.items()}
            title = row.get('title') or row.get('role') or row.get('job title') or row.get('position') or 'Untitled role'
            company = row.get('company') or row.get('employer') or ''
            location = row.get('location') or ''
            url = row.get('url') or row.get('link') or ''
            platform = row.get('platform') or _detect_platform(url, ' '.join(row.values())) or 'Unknown'
            description = row.get('description') or row.get('job description') or row.get('details') or ''
            if not description:
                description = '\n'.join([f'{k.title()}: {v}' for k, v in row.items() if v])
            else:
                description = '\n'.join([
                    f'Title: {title}',
                    f'Company: {company}' if company else '',
                    f'Location: {location}' if location else '',
                    f'URL: {url}' if url else '',
                    description,
                ]).strip()
            rows.append(JobPosting(
                title=title,
                company=company,
                location=location,
                url=url,
                platform=platform,
                description=description,
            ))
    except Exception:
        return []
    return rows


def _parse_one_block(block: str) -> JobPosting:
    lines = [normalize_text(line).strip() for line in block.splitlines() if normalize_text(line).strip()]
    url = _first_url(block)
    platform = _detect_platform(url, block)
    title = ''
    company = ''
    location = ''

    labelled = {}
    for line in lines[:12]:
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        key = key.strip().lower()
        value = value.strip()
        if key in {'title', 'role', 'job title', 'position', 'company', 'employer', 'location', 'platform', 'url', 'link'}:
            labelled[key] = value

    title = labelled.get('title') or labelled.get('role') or labelled.get('job title') or labelled.get('position') or ''
    company = labelled.get('company') or labelled.get('employer') or ''
    location = labelled.get('location') or ''
    platform = labelled.get('platform') or platform
    url = labelled.get('url') or labelled.get('link') or url

    if not title:
        for line in lines:
            if line == url or line.lower().startswith(('http://', 'https://', 'www.')):
                continue
            if ':' in line and line.split(':', 1)[0].strip().lower() in {'company', 'location', 'platform', 'url', 'link'}:
                continue
            title = line[:140]
            break

    return JobPosting(
        title=title or 'Untitled role',
        company=company,
        location=location,
        url=url,
        platform=platform or 'Unknown',
        description=block,
    )


def rank_job_postings(raw_text: str, resume_dict: dict, limit: int = 25, preferences: dict | None = None) -> list[dict]:
    resume_text = resume_json_to_text(resume_dict)
    resume_terms = _important_terms(resume_text)
    postings = parse_job_postings(raw_text)
    ranked = [_score_posting(posting, resume_terms, preferences or {}) for posting in postings]
    ranked.sort(key=lambda row: row['score'], reverse=True)
    return ranked[:limit]


def _score_posting(posting: JobPosting, resume_terms: set[str], preferences: dict) -> dict:
    text = normalize_text(posting.description).lower()
    job_terms = _important_terms(text)
    matched = sorted(job_terms & resume_terms)
    missing = sorted(job_terms - resume_terms)
    positive_hits = sorted({signal for signal in POSITIVE_SIGNALS if signal in text})
    negative_hits = sorted({signal for signal in NEGATIVE_SIGNALS if signal in text})
    classification = classify_job_heuristic(posting.description)

    overlap_score = int((len(matched) / max(1, min(len(job_terms), 40))) * 65)
    positive_score = min(25, len(positive_hits) * 4)
    category_bonus = 10 if classification.get('primary_category') in {'software_engineering', 'cybersecurity', 'frontend', 'backend', 'devops'} else 0
    preference_hits, preference_bonus, preference_penalty = _score_preferences(posting, classification, preferences, negative_hits)
    penalty = min(45, len(negative_hits) * 8 + preference_penalty)
    score = max(0, min(100, overlap_score + positive_score + category_bonus + preference_bonus - penalty))
    reasons = _build_reasons(
        matched=matched,
        positive_hits=positive_hits,
        negative_hits=negative_hits,
        preference_hits=preference_hits,
        classification=classification,
        score=score,
    )

    if score >= 70 and not negative_hits:
        recommendation = 'APPLY'
    elif score >= 45:
        recommendation = 'REVIEW'
    else:
        recommendation = 'SKIP'

    return {
        'id': hashlib.sha256(posting.description.encode('utf-8', errors='replace')).hexdigest()[:12],
        'title': posting.title,
        'company': posting.company,
        'location': posting.location,
        'url': posting.url,
        'platform': posting.platform,
        'description': posting.description,
        'score': score,
        'recommendation': recommendation,
        'detected_role_type': classification.get('primary_category', 'unknown'),
        'detected_confidence': classification.get('confidence', 0),
        'matched_terms': matched[:12],
        'missing_terms': missing[:8],
        'positive_signals': positive_hits[:8],
        'risk_signals': negative_hits[:8],
        'preference_signals': preference_hits[:8],
        'reasons': reasons,
    }


def _score_preferences(posting: JobPosting, classification: dict, preferences: dict, negative_hits: list[str]) -> tuple[list[str], int, int]:
    text = normalize_text(' '.join([posting.title, posting.company, posting.location, posting.description])).lower()
    hits: list[str] = []
    bonus = 0
    penalty = 0

    preferred_locations = [
        normalize_text(str(x)).strip().lower()
        for x in re.split(r'[,\n]', str(preferences.get('preferred_locations') or ''))
        if normalize_text(str(x)).strip()
    ]
    if preferred_locations:
        if any(loc in text for loc in preferred_locations) or 'remote' in text:
            hits.append('preferred location/remote')
            bonus += 8
        else:
            penalty += 5

    role_focus = normalize_text(str(preferences.get('role_focus') or 'both')).lower()
    category = str(classification.get('primary_category') or 'unknown')
    if role_focus == 'software' and category in {'software_engineering', 'frontend', 'backend', 'devops'}:
        hits.append('software-focused role')
        bonus += 8
    elif role_focus == 'cyber' and category == 'cybersecurity':
        hits.append('cybersecurity-focused role')
        bonus += 8
    elif role_focus in {'software', 'cyber'} and category != 'unknown':
        penalty += 5

    if preferences.get('prefer_junior'):
        if any(term in text for term in ('graduate', 'junior', 'entry level', 'entry-level', 'associate')):
            hits.append('junior/graduate friendly')
            bonus += 10
        elif negative_hits:
            penalty += 8

    if preferences.get('avoid_senior') and negative_hits:
        hits.append('senior/lead risk penalised')
        penalty += 10

    return hits, min(30, bonus), min(30, penalty)


def _build_reasons(*, matched: list[str], positive_hits: list[str], negative_hits: list[str], preference_hits: list[str], classification: dict, score: int) -> list[str]:
    reasons = []
    category = str(classification.get('primary_category') or 'unknown').replace('_', ' ')
    if category != 'unknown':
        reasons.append(f'Detected as {category}.')
    if matched:
        reasons.append(f'Matched resume terms: {", ".join(matched[:5])}.')
    if positive_hits:
        reasons.append(f'Positive signals: {", ".join(positive_hits[:4])}.')
    if preference_hits:
        reasons.append(f'Preference match: {", ".join(preference_hits[:3])}.')
    if negative_hits:
        reasons.append(f'Risk signals: {", ".join(negative_hits[:3])}.')
    reasons.append(f'Overall score: {score}/100.')
    return reasons[:5]


def _important_terms(text: str) -> set[str]:
    norm = normalize_text(text or '').lower()
    phrases = {
        phrase for phrase in POSITIVE_SIGNALS + NEGATIVE_SIGNALS
        if phrase in norm and len(phrase) >= 4
    }
    tokens = {
        token
        for token in re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-\.]{2,}', norm)
        if token not in {'the', 'and', 'for', 'with', 'you', 'our', 'are', 'will', 'this', 'that', 'from', 'your', 'role', 'work'}
    }
    return phrases | tokens


def _first_url(text: str) -> str:
    match = re.search(r'(https?://[^\s<>"\']+|www\.[^\s<>"\']+)', text or '', flags=re.IGNORECASE)
    if not match:
        return ''
    raw = match.group(1).rstrip('.,);:!?')
    return raw if raw.lower().startswith(('http://', 'https://')) else f'https://{raw}'


def _detect_platform(url: str, text: str) -> str:
    host = urlparse(url).netloc.lower() if url else ''
    for domain, label in PLATFORM_HINTS.items():
        if domain in host:
            return label
    lowered = (text or '').lower()
    for domain, label in PLATFORM_HINTS.items():
        if domain.replace('.com.au', '').replace('.com', '') in lowered:
            return label
    return ''
