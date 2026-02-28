# Resume Bot Regression Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 6 regressions in tools/resume_bot.py: third-person summary, tagline hallucination, skills truncation/grouping, duplicate section entries, missing projects, and cover letter tagline quality.

**Architecture:** All changes are in `tools/resume_bot.py` (and one small change to `web_app.py` for Fix 6). Each fix is isolated. Commit after each fix group.

**Tech Stack:** Python 3.12, Flask, Anthropic SDK (`claude-sonnet-4-6`)

---

## Fix 1: Third-Person Summary (system prompt addition)

**Files:**
- Modify: `tools/resume_bot.py` — `tailor_resume_with_ai()`, line ~1774

**Change:** In `tailor_resume_with_ai()`, find the line:
```python
"Professional Summary must lead with Bunkerify as proof of production impact and mention current Master of Computer Science (Cybersecurity) studies at UOW.\n\n"
```
Add immediately after it:
```python
"Professional Summary must be written in first person without using the candidate's name. "
"Do not start with the candidate's name. Do not use third-person phrasing.\n\n"
```

---

## Fix 2: Tagline Hallucination — ASP.NET and Dot-Separated Terms

**Files:**
- Modify: `tools/resume_bot.py` — `_validate_tagline()`, lines 1420–1439

**Problem:** Current tokenizer `re.findall(r'[a-zA-Z][a-zA-Z0-9\+\#\-]+', tagline.lower())` strips dots, so `ASP.NET` → tokens `['asp', 'net']`. The word `net` appears in resume URLs so it passes validation incorrectly.

**Replace the entire `_validate_tagline` function** with this new version that:
- Checks dot-separated terms (e.g. `ASP.NET`, `.NET`, `Node.js`) as full strings BEFORE tokenizing
- Splits hyphenated compounds (e.g. `Cybersecurity-Focused`) and checks each part
- Expands stop words with generic role/skill modifiers

```python
def _validate_tagline(tagline: str, resume_text: str) -> str | None:
    """Validate every meaningful word in the tagline appears in the resume.

    Dot-separated terms (ASP.NET, Node.js) are checked as complete strings.
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
```

---

## Fix 3: Skills Truncation and AWS Grouping

### 3a: System prompt — AWS grouping rule

**Files:**
- Modify: `tools/resume_bot.py` — `tailor_resume_with_ai()`, line ~1776

In the system prompt, find:
```python
"Key Skills / Technical Skills should be tailored to the job and include only relevant skills from my resume.\n\n"
```
Add immediately after:
```python
"Group all AWS services under a single tag formatted as 'AWS (EC2, S3, IAM, CloudWatch)'. "
"Do not emit EC2, S3, RDS, IAM, CloudWatch, CloudTrail, or any individual AWS service as a separate skill tag.\n\n"
```

### 3b: Add `_split_skills_csv()` — paren-depth-aware comma splitter

**Files:**
- Modify: `tools/resume_bot.py` — add after `clean_skill_token()` (~line 107)

```python
def _split_skills_csv(text: str) -> list[str]:
    """Split a comma-separated skill list respecting parentheses.

    'AWS (EC2, S3, IAM), TypeScript' → ['AWS (EC2, S3, IAM)', 'TypeScript']
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
```

### 3c: Fix `clean_skill_token()` — preserve internal parens, balance unmatched

**Files:**
- Modify: `tools/resume_bot.py` — `clean_skill_token()`, lines 101–106

**Problem:** Current code blindly strips ALL trailing `)` chars, so `React Native (cross-platform iOS and Android)` → `React Native (cross-platform iOS and Android` (truncated).

**Replace `clean_skill_token`:**
```python
def clean_skill_token(skill: str) -> str:
    token = normalize_text(skill).strip()
    # Strip outer-wrapping brackets ONLY when the whole token is wrapped:
    # e.g. "(React Native)" -> "React Native", but NOT "React Native (iOS)" -> unchanged
    if len(token) >= 2 and token[0] == '(' and token[-1] == ')':
        inner = token[1:-1].strip()
        if inner:
            token = inner
    elif len(token) >= 2 and token[0] == '[' and token[-1] == ']':
        inner = token[1:-1].strip()
        if inner:
            token = inner
    token = token.strip(' -;:,.')
    # Balance unmatched open parens: "AWS (EC2, S3" → "AWS (EC2, S3)"
    open_count = token.count('(')
    close_count = token.count(')')
    if open_count > close_count:
        token = token + ')' * (open_count - close_count)
    elif close_count > open_count:
        # Strip excess trailing close parens: "CloudWatch)" → "CloudWatch"
        excess = close_count - open_count
        while excess > 0 and token.endswith(')'):
            token = token[:-1]
            excess -= 1
    return token.strip()
```

### 3d: Use `_split_skills_csv` in `build_sections_from_tailored_text()`

**Files:**
- Modify: `tools/resume_bot.py` line ~976

Find:
```python
for part in [p.strip() for p in item.split(',')]:
```
Replace with:
```python
for part in _split_skills_csv(item):
```

---

## Fix 4: Duplicate Entries — Education, Certifications, Additional Information

**Files:**
- Modify: `tools/resume_bot.py` — `_prioritize_tailored_sections()`, lines 1126–1142

### 4a: Dedup Education entries by title

After the existing education sort block, BEFORE the sort, add a dedup pass.

Find:
```python
    education = _ensure_section(sections, 'Education')
    if education.get('entries'):
        def edu_key(entry):
            title_l = normalize_text(entry.get('title', '')).lower()
            if 'bachelor' in title_l:
                return (0, title_l)
            if 'master' in title_l:
                return (1, title_l)
            return (2, title_l)
        education['entries'] = sorted(education['entries'], key=edu_key)
```
Replace with:
```python
    education = _ensure_section(sections, 'Education')
    if education.get('entries'):
        # Dedup by normalised title before sorting
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
```

### 4b: Dedup Certifications entries

Find:
```python
    certs = _ensure_section(sections, 'Certifications')
    cert_blob = ' '.join(certs.get('paragraphs', []) + certs.get('bullets', []))
    for e in certs.get('entries', []):
        cert_blob += ' ' + e.get('title', '') + ' ' + e.get('subtitle', '')
    if 'aws academy cloud foundations' not in normalize_text(cert_blob).lower():
        certs['bullets'].append('AWS Academy Graduate - AWS Academy Cloud Foundations award')
```
Replace with:
```python
    certs = _ensure_section(sections, 'Certifications')
    # Dedup cert entries by title
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
```

### 4c: Dedup Additional Information entries (driving roles)

After the existing `moved_driving` insertion block (after line ~1101), add a final dedup pass for all Additional Information entries:

Find the end of the `if moved_driving:` block (after `additional['entries'].append(entry)`), then add:
```python
    # Final dedup of all Additional Information entries by (title, date)
    seen_add: set[tuple[str, str]] = set()
    deduped_add = []
    for e in additional['entries']:
        key = (
            normalize_text(e.get('title', '')).lower(),
            normalize_text(e.get('date', '')).lower(),
        )
        if key not in seen_add:
            seen_add.add(key)
            deduped_add.append(e)
    additional['entries'] = deduped_add
```

---

## Fix 5: Unconditional Injection of Critical Projects

**Files:**
- Modify: `tools/resume_bot.py` — `_prioritize_tailored_sections()`, lines 1103–1116

**Problem:** Job Application Assistant (rank=2) and Cancer Awareness (rank=3) are dropped intermittently. The current fallback code is guarded by `if fallback_project_entries:` which can be falsy.

After the existing fallback block, add an unconditional check for ranks 2 and 3:

Find:
```python
    if fallback_project_entries:
        existing_project_keys = set()
        for entry in projects.get('entries', []):
            rank = _project_rank(entry.get('title', ''))
            if rank is not None:
                existing_project_keys.add(rank)
        for entry in fallback_project_entries:
            rank = _project_rank(entry.get('title', ''))
            if rank is None or rank in existing_project_keys:
                continue
            projects['entries'].append(dict(entry))
            existing_project_keys.add(rank)
```
Replace with:
```python
    existing_project_keys: set[int] = set()
    for entry in projects.get('entries', []):
        rank = _project_rank(entry.get('title', ''))
        if rank is not None:
            existing_project_keys.add(rank)

    if fallback_project_entries:
        for entry in fallback_project_entries:
            rank = _project_rank(entry.get('title', ''))
            if rank is None or rank in existing_project_keys:
                continue
            projects['entries'].append(dict(entry))
            existing_project_keys.add(rank)

    # Unconditional: Job Application Assistant (rank=2) and Cancer Awareness (rank=3)
    # must always appear. If absent from AI output AND from fallback, skip gracefully.
    _MUST_HAVE_RANKS = {2, 3}
    for must_rank in sorted(_MUST_HAVE_RANKS - existing_project_keys):
        for entry in (fallback_project_entries or []):
            if _project_rank(entry.get('title', '')) == must_rank:
                projects['entries'].append(dict(entry))
                existing_project_keys.add(must_rank)
                break
```

---

## Fix 6: Cover Letter Tagline Quality

**Problem:** `generate_cover_letter()` calls `generate_tagline_with_ai()` which asks for "3 to 6 words, no pipes" — producing poor taglines like "Graduate Software Engineer Web Mobile". The cover letter should use the same pipe-separated tagline as the resume.

### 6a: `generate_resume()` returns the tagline

**Files:**
- Modify: `tools/resume_bot.py` — `generate_resume()`, lines 1821+

`generate_resume()` currently returns `(html_path, pdf_path)`. Change it to return `(html_path, pdf_path, tagline)` where `tagline` is the string used in the resume header (or `None` in the non-AI path).

In the AI path, after:
```python
        ai_header_html = header_html
```
The `tagline` variable is already in scope. Ensure the function returns it.

At the function's return in the AI path, change:
```python
        return html_path, pdf_path
```
To:
```python
        return html_path, pdf_path, tagline
```

In the non-AI path return, change:
```python
        return html_path, pdf_path
```
To:
```python
        return html_path, pdf_path, None
```

### 6b: `generate_cover_letter()` accepts an optional tagline

**Files:**
- Modify: `tools/resume_bot.py` — `generate_cover_letter()` signature, line 1571

Change:
```python
def generate_cover_letter(
    resume_path,
    job_text: str,
    out_dir,
    label: str | None = None,
    template_path=None,
):
```
To:
```python
def generate_cover_letter(
    resume_path,
    job_text: str,
    out_dir,
    label: str | None = None,
    template_path=None,
    tagline: str | None = None,
):
```

Then find the tagline generation block:
```python
    if (job_text or '').strip():
        tagline = generate_tagline_with_ai(job_text=job_text, resume_text=resume_text)
        if tagline:
            header_html = _apply_tagline_to_header(header_html, tagline)
```
Replace with:
```python
    if (job_text or '').strip():
        cover_tagline = tagline or generate_tagline_with_ai(job_text=job_text, resume_text=resume_text)
        if cover_tagline:
            header_html = _apply_tagline_to_header(header_html, cover_tagline)
```

### 6c: `web_app.py` passes tagline through

**Files:**
- Modify: `web_app.py`, lines ~483–503

Change:
```python
            html_path, pdf_path = generate_resume(...)
```
To:
```python
            html_path, pdf_path, resume_tagline = generate_resume(...)
```

And:
```python
                cover_path, cover_pdf_path, cover_text = generate_cover_letter(
                    resume_path=DEFAULT_RESUME,
                    job_text=job_text,
                    out_dir=OUTPUT_DIR,
                    label=label,
                    template_path=DEFAULT_TEMPLATE,
                )
```
To:
```python
                cover_path, cover_pdf_path, cover_text = generate_cover_letter(
                    resume_path=DEFAULT_RESUME,
                    job_text=job_text,
                    out_dir=OUTPUT_DIR,
                    label=label,
                    template_path=DEFAULT_TEMPLATE,
                    tagline=resume_tagline,
                )
```

### 6d: Update `generate_tagline_with_ai()` prompt to pipe format

**Files:**
- Modify: `tools/resume_bot.py` — `generate_tagline_with_ai()`, line ~1442

Change the prompt from:
```python
"Create a very short, role-specific resume tagline based on the job description and the resume. "
"Return a single line only, no quotes, no extra text. "
"Use 3 to 6 words maximum. Avoid separators like '·' or '|'. "
"STRICT RULE: Use only roles/skills/terms that already appear in the resume text. "
"Do NOT invent or add new tools, skills, or roles.\n\n"
```
To:
```python
"Create a role-specific resume tagline based on the job description and the resume. "
"Return a single line only, no quotes, no extra text. "
"Format: Role | Skill | Skill | Skill (pipe-separated, 3-4 segments). "
"STRICT RULE: Use only roles/skills/terms that already appear in the resume text provided. "
"Do NOT invent or add new tools, skills, technologies, or roles.\n\n"
```

---

## Commit Plan

After all 6 fixes are applied and syntax-checked:
```bash
cd C:\Users\mobee\Desktop\job-application-assistant
python -m py_compile tools/resume_bot.py web_app.py
git add tools/resume_bot.py web_app.py
git commit -m "fix: 6 resume bot regressions (summary, tagline, skills, dupes, projects, cover letter)"
git push origin main
```

Then on the VM:
```bash
cd /home/ubuntu/job-application-assistant
git pull origin main
sudo systemctl restart resume-tailor.service
```
