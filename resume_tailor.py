"""Per-job resume tailoring that cannot invent facts.

Pipeline (POST /api/tailor-resume, used by Applier-Engine):

1. parse_resume()  - Gemini splits the plain-text resume into sections. Every
   string it returns is checked to occur VERBATIM in the original text
   (whitespace-insensitive), so the parse itself can't add anything. Cached
   per resume hash.
2. tailor()        - Gemini proposes, for one job description: a rewritten
   profile summary, an order for the career highlights, per-job bullet order
   with optional light rewording, and a skills order. It can only reference
   existing bullets/skills by id.
3. validate_and_merge() - deterministic, no AI. Header, companies, titles,
   locations, dates, certifications and education are always copied from the
   parse. Each reworded sentence must pass guard_rewrite():
     - every number in it already appears in its source sentence,
     - no word that is in the job description but not in the resume
       (keyword stuffing, e.g. adding "Kubernetes"),
     - no new capitalized term / tool / token with digits or symbols,
     - not much longer than the source.
   A failing sentence silently reverts to the original wording; every
   decision is reported in `changes` so a human can audit it.
4. render_resume_pdf() - plain single-column PDF (ATS-friendly text).

If anything structural fails (unparseable output, a parse that isn't
verbatim), the caller gets ok=False and must use the original resume file.
"""
import hashlib
import json
import re

# Lowercase words that may appear in a rewrite even though the resume doesn't
# use them: grammar and neutral verbs only - nothing that asserts a skill or
# scope of ownership (led/owned/managed/architected are allowed only if the
# resume itself already uses them).
GENERIC_WORDS = set("""
a an and the of to in for on with by at from as into across through using via that which this these those its it their our
is are was were be been has have having or nor but so while including include includes included over under per each all both other more most
built build building designed design designing delivered deliver
created create developed develop improved improve improving increased increase reduced reduce reducing cut cutting
established establish implemented implement enabled enable ensured ensure helped help helping
supported support providing provided automated automate validated validate
end-to-end end to resulting result results achieving achieved achieve contributing contributed
key core critical high large new modern robust reliable scalable
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./&'-]*")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _squash(s):
    return re.sub(r"\s+", "", (s or "")).lower().replace("•", "")


def _words(s):
    return {w.lower().strip(".,;:'") for w in _WORD.findall(s or "")}


def _numbers(s):
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(s or "")}


def guard_rewrite(new, source, resume_text, jd_text, max_growth=1.35, same_verb=False):
    """Returns a list of reasons this rewrite is not allowed (empty = OK)."""
    reasons = []
    new = (new or "").strip()
    if not new:
        return ["empty"]
    if same_verb:
        first = lambda t: (t.split() or [""])[0].lower().strip(".,;:")
        if first(new) != first(source):
            reasons.append(f"must start with the original verb '{(source.split() or [''])[0]}'")
    extra_nums = _numbers(new) - _numbers(source)
    if extra_nums:
        reasons.append(f"new numbers {sorted(extra_nums)}")
    resume_words = _words(resume_text)
    new_words = _words(new) - resume_words
    jd_words = _words(jd_text)
    stuffed = sorted(w for w in new_words if w in jd_words and w not in GENERIC_WORDS)
    if stuffed:
        reasons.append(f"job-description words not in resume {stuffed}")
    # Capitalized / technical-looking tokens must already exist in the resume.
    for tok in _WORD.findall(new):
        t = tok.lower().strip(".,;:'")
        if t in resume_words or t in GENERIC_WORDS:
            continue
        if tok[0].isupper() and new.find(tok) > 0 or re.search(r"[\d+#/&.]", tok.strip(".,;:")):
            reasons.append(f"new term '{tok}'")
    other = sorted(w for w in new_words if w not in GENERIC_WORDS and w not in jd_words)
    if len(other) > 2:
        reasons.append(f"too many new words {other[:6]}")
    if len(new) > max_growth * max(len(source), 80):
        reasons.append("much longer than original")
    return reasons


# ---------------------------------------------------------------- parsing

PARSE_PROMPT = """Split this resume into JSON. Copy every string EXACTLY as it appears in the resume
(same words, same punctuation; you may only drop bullet symbols and join wrapped lines).
Never summarize, never add anything.

Schema:
{{"name": "", "headline": "", "contact": "", "profile": "",
  "certifications": [""], "highlights": [""],
  "experience": [{{"title": "", "company": "", "location": "", "dates": "", "bullets": [""]}}],
  "skills": [{{"category": "", "items": [""]}}],
  "education": [""]}}

Resume:
---
{resume}
---"""

_parse_cache = {}


def _json_call(generate, prompt, attempts=3):
    """Gemini's JSON mode still occasionally emits malformed JSON - retry."""
    last = None
    for _ in range(attempts):
        raw = generate(prompt, json_mode=True, thinking_budget=0)
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            last = ValueError("model returned non-object JSON")
        except (json.JSONDecodeError, TypeError) as e:
            last = e
    raise last


def _all_strings(parsed):
    out = []
    for k in ("name", "headline", "contact", "profile"):
        out.append(parsed.get(k, ""))
    out += parsed.get("certifications", []) + parsed.get("highlights", []) + parsed.get("education", [])
    for job in parsed.get("experience", []):
        out += [job.get("title", ""), job.get("company", ""), job.get("location", ""), job.get("dates", "")]
        out += job.get("bullets", [])
    for s in parsed.get("skills", []):
        out.append(s.get("category", ""))
        out += s.get("items", [])
    return [s for s in out if isinstance(s, str) and s.strip()]


def unaccounted_text(resume_text, parsed):
    """Resume text not covered by any parsed string (minus headings/separators)."""
    leftover = _squash(resume_text)
    for piece in sorted((_squash(x) for x in _all_strings(parsed)), key=len, reverse=True):
        leftover = leftover.replace(piece, "", 1)
    return re.sub(r"profile|summary|certifications?|careerhighlights|professionalexperience|experience|technicalskills|skills|education|[|\t\-:,.\u2013\u2014\u2022]", "", leftover)


def parse_resume(resume_text, generate):
    """Structured, verbatim-checked parse. Raises ValueError if not verbatim."""
    key = hashlib.sha256(resume_text.encode()).hexdigest()
    if key in _parse_cache:
        return _parse_cache[key]
    parsed = _json_call(generate, PARSE_PROMPT.format(resume=resume_text))
    hay = _squash(resume_text)
    missing = [s for s in _all_strings(parsed) if _squash(s) not in hay]
    if missing:
        raise ValueError(f"resume parse not verbatim: {missing[:3]}")
    if not parsed.get("experience"):
        raise ValueError("resume parse found no experience")
    # Completeness: removing every parsed string from the resume must leave only
    # headings/separators. Catches dropped or truncated bullets.
    leftover = unaccounted_text(resume_text, parsed)
    if len(leftover) > 12:
        raise ValueError(f"resume parse incomplete (unaccounted text: {leftover[:60]!r})")
    _parse_cache[key] = parsed
    return parsed


# ---------------------------------------------------------------- tailoring

TAILOR_PROMPT = """You are tailoring a resume to one job WITHOUT adding anything that is not already true.

Rules:
- You may only reorder existing items and lightly reword existing sentences to put the most
  relevant facts first. Reference items by their ids.
- Never add a skill, tool, technology, metric, number, employer, title, responsibility or
  claim that is not in the original sentence. Do NOT copy keywords from the job description
  unless the same word is already in the resume.
- Keep every number exactly as written. If unsure, keep the original wording.\n- A reworded bullet must start with the SAME action verb as the original and read as one\n  grammatical sentence; usually just move the most relevant clause earlier.
- "summary": 2-3 sentences in resume style (no "I"), built only from facts and phrases in the
  resume's PROFILE and bullets, leading with what matters most for this job.

Return JSON:
{{"summary": "",
  "highlight_order": ["h0", ...],
  "experience": [{{"job": 0, "bullets": [{{"id": "e0b0", "text": "reworded or original sentence"}}]}}],
  "skill_order": [{{"category_id": "s0", "items": ["existing item", ...]}}],
  "requirements": ["short term", ...]}}

"requirements": the 10-20 most important CONCRETE skills, tools, technologies and practices the
job asks for, each a 1-3 word term as written in the job description (e.g. "Playwright",
"GraphQL", "GitHub Actions", "API testing", "performance testing", "mentoring"). No soft skills
("communication", "team player"), no job titles, no company jargon. List them whether or not the
resume has them - this is used to show the candidate real gaps.

Job description:
---
{jd}
---

Resume items:
{items}"""


ORDER_PROMPT = """Reorder this candidate's resume items so the ones most relevant to the job come first.
You only return ids - never text. Every id must be one shown below.

Return JSON:
{{"highlight_order": ["h1", "h0", ...],
  "experience": [{{"job": 0, "order": ["e0b3", "e0b0", ...]}}],
  "skill_order": ["s2", "s0", ...],
  "requirements": ["short term", ...]}}

"requirements": the 10-20 most important CONCRETE skills, tools, technologies and practices the
job asks for, each a 1-3 word term as written in the job description (e.g. "Playwright",
"GraphQL", "GitHub Actions", "API testing", "mentoring"). No soft skills, no job titles, no
company jargon. List them whether or not the resume has them.

Job description:
---
{jd}
---

Resume items:
{items}"""


def _order_to_proposal(order):
    """ids-only answer -> the proposal shape validate_and_merge expects (no text)."""
    return {
        "summary": "",
        "highlight_order": order.get("highlight_order") or [],
        "experience": [{"job": e.get("job"), "bullets": [{"id": i, "text": ""} for i in (e.get("order") or [])]}
                       for e in (order.get("experience") or [])],
        "skill_order": [{"category_id": c, "items": []} for c in (order.get("skill_order") or [])],
        "requirements": order.get("requirements") or [],
    }


def _items_for_prompt(parsed):
    lines = [f"PROFILE: {parsed.get('profile', '')}"]
    for i, h in enumerate(parsed.get("highlights", [])):
        lines.append(f"h{i}: {h}")
    for j, job in enumerate(parsed["experience"]):
        lines.append(f"JOB {j}: {job.get('title')} | {job.get('company')} | {job.get('dates')}")
        for b, text in enumerate(job.get("bullets", [])):
            lines.append(f"  e{j}b{b}: {text}")
    for i, s in enumerate(parsed.get("skills", [])):
        lines.append(f"s{i} {s.get('category')}: {', '.join(s.get('items', []))}")
    return "\n".join(lines)


def _ordered(ids, n, prefix):
    """Valid unique ids from the model's order, then any it left out (nothing is dropped)."""
    seen, out = set(), []
    for x in ids or []:
        m = re.fullmatch(prefix + r"(\d+)", str(x))
        if m and int(m.group(1)) < n and int(m.group(1)) not in seen:
            seen.add(int(m.group(1)))
            out.append(int(m.group(1)))
    return out + [i for i in range(n) if i not in seen]


def validate_and_merge(parsed, proposal, resume_text, jd_text, allow_rewording=False):
    """Deterministic merge of the model's proposal onto the verbatim parse.

    allow_rewording=False (the default): only ORDER is taken from the model -
    every sentence, the summary and every skill line stay word-for-word.
    """
    changes = {"summary": None, "reworded": [], "reverted": [], "reordered": False}
    if not allow_rewording:
        proposal = dict(proposal)
        proposal["summary"] = ""
        proposal["experience"] = [
            {"job": e.get("job"), "bullets": [{"id": b.get("id"), "text": ""} for b in (e.get("bullets") or [])]}
            for e in (proposal.get("experience") or [])
        ]
    out = {k: parsed.get(k) for k in ("name", "headline", "contact", "certifications", "education")}

    # Summary: checked against the whole resume (any number must exist somewhere in it).
    summary = (proposal.get("summary") or "").strip()
    reasons = guard_rewrite(summary, resume_text, resume_text, jd_text, max_growth=10) if summary else ["not rewritten (reorder-only mode)" if not allow_rewording else "empty"]
    if summary and not reasons and len(summary) <= 600:
        out["profile"] = summary
        changes["summary"] = "rewritten"
    else:
        out["profile"] = parsed.get("profile", "")
        changes["summary"] = f"original kept ({'; '.join(reasons) or 'too long'})"

    highlights = parsed.get("highlights", [])
    order = _ordered(proposal.get("highlight_order"), len(highlights), "h")
    out["highlights"] = [highlights[i] for i in order]
    changes["reordered"] = order != list(range(len(highlights)))

    by_job = {}
    for entry in proposal.get("experience") or []:
        try:
            by_job[int(entry.get("job"))] = entry.get("bullets") or []
        except (TypeError, ValueError):
            continue
    out["experience"] = []
    for j, job in enumerate(parsed["experience"]):
        originals = job.get("bullets", [])
        proposed = by_job.get(j, [])
        texts = {}
        for b in proposed:
            m = re.fullmatch(rf"e{j}b(\d+)", str(b.get("id", "")))
            if m and int(m.group(1)) < len(originals):
                texts.setdefault(int(m.group(1)), (b.get("text") or "").strip())
        order = _ordered([f"e{j}b{i}" for i in texts], len(originals), f"e{j}b")
        bullets = []
        for i in order:
            src = originals[i]
            new = texts.get(i) or src
            if _squash(new) != _squash(src):
                why = guard_rewrite(new, src, resume_text, jd_text, same_verb=True)
                if why:
                    changes["reverted"].append({"original": src, "proposed": new, "why": why})
                    new = src
                else:
                    changes["reworded"].append({"original": src, "new": new})
            bullets.append(new)
        if order != list(range(len(originals))):
            changes["reordered"] = True
        out["experience"].append({**{k: job.get(k) for k in ("title", "company", "location", "dates")}, "bullets": bullets})

    skills = parsed.get("skills", [])
    proposed_skills = {}
    for s in proposal.get("skill_order") or []:
        m = re.fullmatch(r"s(\d+)", str(s.get("category_id", "")))
        if m and int(m.group(1)) < len(skills):
            proposed_skills.setdefault(int(m.group(1)), s.get("items") or [])
    out["skills"] = []
    for i in _ordered([f"s{i}" for i in proposed_skills], len(skills), "s"):
        original_items = skills[i].get("items", [])
        lookup = {x.lower(): x for x in original_items}
        items, seen = [], set()
        for x in proposed_skills.get(i, []):
            k = str(x).strip().lower()
            if k in lookup and k not in seen:  # only existing items, in the proposed order
                seen.add(k)
                items.append(lookup[k])
        items += [x for x in original_items if x.lower() not in seen]  # never drop a skill
        entry = {"category": skills[i].get("category"), "items": items}
        if skills[i].get("raw"):
            entry["raw"] = skills[i]["raw"]
        out["skills"].append(entry)
    return out, changes


def tailor(resume_text, jd_text, generate, allow_rewording=False, parsed=None):
    """Full pipeline. Returns (parsed_original, tailored, changes, ats_report). Raises on structural failure."""
    parsed = parsed or parse_resume(resume_text, generate)
    items = _items_for_prompt(parsed)
    if allow_rewording:
        proposal = _json_call(generate, TAILOR_PROMPT.format(jd=jd_text[:12000], items=items))
    else:
        # Reorder-only: ask for ids, not text - far fewer output tokens.
        proposal = _order_to_proposal(_json_call(generate, ORDER_PROMPT.format(jd=jd_text[:12000], items=items)))
    tailored, changes = validate_and_merge(parsed, proposal, resume_text, jd_text, allow_rewording)
    return parsed, tailored, changes, ats_report(proposal.get("requirements") or [], tailored)


# ---------------------------------------------------------------- ATS match report

_SYNONYMS = {
    "ci/cd": ["ci/cd", "continuous integration", "jenkins", "harness"],
    "test automation": ["test automation", "automation framework", "automation platform"],
    "api testing": ["api testing", "rest api", "api quality", "api tester", "integration tests"],
    "performance testing": ["performance", "jmeter", "blazemeter"],
    "javascript": ["javascript"], "typescript": ["typescript"],
}


_STOP = set("and or of the a an in for to with on at using via & experience knowledge strong proven".split())


def _stem(w):
    for suf in ("ship", "ing", "ers", "er", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _term_in(term, text):
    """Exact phrase, or every content word of the term (stemmed) present in the text."""
    if _phrase_in(term, text):
        return True
    words = [_stem(w) for w in re.findall(r"[a-z0-9+#/.-]+", term.lower()) if w not in _STOP]
    if not words:
        return False
    text_stems = {_stem(w) for w in re.findall(r"[a-z0-9+#/.-]+", text)}
    return all(w in text_stems for w in words)


def _phrase_in(term, text):
    t = term.lower().strip()
    if not t:
        return False
    if re.search(r"[a-z0-9]", t[0]) and re.search(r"[a-z0-9]", t[-1]):
        return re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text) is not None
    return t in text


def ats_report(requirements, r):
    """Deterministic: which JD requirements literally appear in the resume, and where.

    Found = the term (or a listed equivalent) appears in that section's text.
    Nothing here changes the resume; missing terms are gaps to learn or
    to genuinely evidence - never to paste in.
    """
    sections = [("profile", r.get("profile") or ""), ("highlights", " ".join(r.get("highlights") or []))]
    for job in r.get("experience") or []:
        sections.append((job.get("company") or "experience", " ".join(job.get("bullets") or [])))
    sections.append(("skills", " ".join(f"{s.get('category')}: {', '.join(s.get('items') or [])}" for s in r.get("skills") or [])))
    sections.append(("certifications", " ".join(r.get("certifications") or [])))
    lowered = [(name, text.lower()) for name, text in sections]
    out, seen = [], set()
    for req in requirements:
        if not isinstance(req, str) or not req.strip() or req.lower() in seen:
            continue
        seen.add(req.lower())
        variants = _SYNONYMS.get(req.lower().strip(), [req])
        where = [name for name, text in lowered if any(_term_in(v, text) for v in variants)]
        out.append({"term": req.strip(), "found": bool(where), "where": where[:3]})
    found = sum(1 for x in out if x["found"])
    return {"matchRate": round(found / len(out), 2) if out else None,
            "matched": [x for x in out if x["found"]], "missing": [x["term"] for x in out if not x["found"]]}


# ---------------------------------------------------------------- Word (.docx) output

def docx_text(docx_bytes):
    """Plain text of a .docx, one paragraph per line (used as the resume text when a .docx is given)."""
    import io
    from docx import Document
    return "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs if p.text.strip())


def parse_docx(docx_bytes):
    """Exact structure from the candidate's own .docx - no AI involved.

    Sections are ALL-CAPS non-list paragraphs; list (numbered/bulleted)
    paragraphs are items; in EXPERIENCE a non-list paragraph with '|' starts a
    job ("Title | Company | Location<TAB>Dates"). Returns None if the layout
    doesn't look like that, so the caller can fall back to the AI parse.
    """
    import io
    from docx import Document

    doc = Document(io.BytesIO(docx_bytes))
    out = {"name": "", "headline": "", "contact": "", "profile": "", "certifications": [],
           "highlights": [], "experience": [], "skills": [], "education": []}
    header, section = [], None
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        ppr = p._p.pPr
        style = (p.style.name if p.style is not None else "") or ""
        is_item = (ppr is not None and ppr.numPr is not None) or style.startswith("List")
        if not is_item and text.isupper() and len(text) < 40:
            section = text
            continue
        if section is None:
            header.append(text)
        elif "PROFILE" in section or "SUMMARY" in section:
            out["profile"] = (out["profile"] + " " + text).strip()
        elif "CERTIF" in section:
            out["certifications"].append(text)
        elif "HIGHLIGHT" in section:
            out["highlights"].append(text)
        elif "EXPERIENCE" in section:
            if not is_item and "|" in text:
                left, _, dates = p.text.partition("\t")
                parts = [x.strip() for x in left.split("|")]
                out["experience"].append({"title": parts[0], "company": parts[1] if len(parts) > 1 else "",
                                          "location": parts[2] if len(parts) > 2 else "",
                                          "dates": dates.strip(), "bullets": []})
            elif out["experience"]:
                out["experience"][-1]["bullets"].append(text)
        elif "SKILL" in section:
            cat, _, items = text.partition(":")
            out["skills"].append({"category": cat.strip(), "items": [x.strip() for x in items.split(",") if x.strip()], "raw": text})
        elif "EDUCATION" in section:
            out["education"].append(text)
    if header:
        out["name"] = header[0]
        out["headline"] = header[1] if len(header) > 1 else ""
        out["contact"] = " ".join(header[2:])
    if not out["experience"] or not all(j["bullets"] for j in out["experience"]):
        return None
    return out


def _move_group(paragraphs, originals, new_order_texts):
    from collections import Counter
    """Reorders the paragraphs holding `originals` into `new_order_texts` order, in place.
    Only when they are contiguous siblings; returns False (no change) otherwise."""
    idx = {}
    for i, p in enumerate(paragraphs):
        k = _squash(p.text)
        if k:
            idx.setdefault(k, []).append(i)
    # The same sentence can appear twice (a career highlight repeated under its
    # job), so find a contiguous window that holds exactly this group's texts.
    want = Counter(_squash(t) for t in originals)
    n = len(originals)
    candidates = sorted({i for t in originals for i in idx.get(_squash(t), [])})
    positions = None
    for start in candidates:
        window = list(range(start, start + n))
        if window[-1] < len(paragraphs) and Counter(_squash(paragraphs[i].text) for i in window) == want:
            positions = window
            break
    if positions is None:
        return False
    by_text = {}
    for text, pos in zip(originals, positions):
        by_text.setdefault(_squash(text), []).append(paragraphs[pos]._p)
    elements = []
    for text in new_order_texts:
        bucket = by_text.get(_squash(text))
        if not bucket:
            return False
        elements.append(bucket.pop(0))
    first = paragraphs[min(positions)]._p
    anchor = first.getprevious()
    parent = first.getparent()
    for el in elements:
        parent.remove(el)
    insert_at = (parent.index(anchor) + 1) if anchor is not None else 0
    for offset, el in enumerate(elements):
        parent.insert(insert_at + offset, el)
    return True


def tailor_docx(docx_bytes, parsed, tailored):
    """Applies the tailored ORDER to the original .docx (formatting untouched).

    Returns (new_docx_bytes, notes). Raises ValueError if the result does not
    contain exactly the same paragraphs as the original (nothing added, lost
    or edited) - the caller then uses the original file.
    """
    import io
    from collections import Counter
    from docx import Document

    doc = Document(io.BytesIO(docx_bytes))
    before = Counter(_squash(p.text) for p in doc.paragraphs if p.text.strip())
    notes = []
    groups = [("highlights", parsed.get("highlights") or [], tailored.get("highlights") or [])]
    for j, job in enumerate(parsed.get("experience") or []):
        groups.append((job.get("company") or f"job {j}", job.get("bullets") or [], tailored["experience"][j]["bullets"]))
    skill_line = lambda s: s.get("raw") or f"{s.get('category')}: {', '.join(s.get('items') or [])}"
    # Skill lines: reorder whole category lines only (item order inside a line is kept as written).
    groups.append(("skills", [skill_line(s) for s in parsed.get("skills") or []],
                   [skill_line(next(o for o in parsed["skills"] if o.get("category") == s.get("category"))) for s in tailored.get("skills") or []]))
    for name, originals, new in groups:
        if [_squash(x) for x in originals] == [_squash(x) for x in new]:
            continue
        moved = _move_group(doc.paragraphs, originals, new)
        notes.append(f"{name}: {'reordered' if moved else 'kept original order (layout not safely reorderable)'}")
    buf = io.BytesIO()
    doc.save(buf)
    after = Counter(_squash(p.text) for p in Document(io.BytesIO(buf.getvalue())).paragraphs if p.text.strip())
    if before != after:
        raise ValueError("tailored .docx paragraphs differ from the original - refusing")
    return buf.getvalue(), notes


def resume_to_text(r):
    lines = [r["name"], r.get("headline") or "", r.get("contact") or "", "", "PROFILE", r["profile"], ""]
    if r.get("certifications"):
        lines += ["CERTIFICATIONS"] + [f"- {c}" for c in r["certifications"]] + [""]
    if r.get("highlights"):
        lines += ["CAREER HIGHLIGHTS"] + [f"- {h}" for h in r["highlights"]] + [""]
    lines.append("PROFESSIONAL EXPERIENCE")
    for job in r["experience"]:
        lines.append(f"{job['title']} | {job['company']} | {job.get('location') or ''} | {job.get('dates') or ''}")
        lines += [f"- {b}" for b in job["bullets"]] + [""]
    lines.append("TECHNICAL SKILLS")
    lines += [f"- {s['category']}: {', '.join(s['items'])}" for s in r["skills"]] + [""]
    lines += ["EDUCATION"] + [f"- {e}" for e in r.get("education") or []]
    return "\n".join(lines)


def render_resume_pdf(r, sanitize):
    """Single-column, text-based PDF (parses cleanly in every ATS)."""
    from fpdf import FPDF

    pdf = FPDF(format="Letter")
    pdf.set_margins(15, 12, 15)
    pdf.set_auto_page_break(True, margin=12)
    pdf.add_page()
    w = pdf.w - pdf.l_margin - pdf.r_margin

    def text(s, size=9.5, style="", h=4.6, align="L"):
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(w, h, sanitize(s or ""), align=align, new_x="LMARGIN", new_y="NEXT")

    def heading(s):
        pdf.ln(1.5)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.cell(w, 5.5, sanitize(s), new_x="LMARGIN", new_y="NEXT")
        y = pdf.get_y()
        pdf.set_draw_color(120, 120, 120)
        pdf.line(pdf.l_margin, y, pdf.l_margin + w, y)
        pdf.ln(1)

    def bullet(s):
        pdf.set_font("Helvetica", "", 9.5)
        pdf.cell(4, 4.6, "-")
        pdf.multi_cell(w - 4, 4.6, sanitize(s), align="L", new_x="LMARGIN", new_y="NEXT")

    text(r["name"], 16, "B", 7, "C")
    if r.get("headline"):
        text(r["headline"], 10.5, "", 5, "C")
    if r.get("contact"):
        text(r["contact"], 9, "", 4.6, "C")
    heading("PROFILE")
    text(r["profile"])
    if r.get("certifications"):
        heading("CERTIFICATIONS")
        for c in r["certifications"]:
            bullet(c)
    if r.get("highlights"):
        heading("CAREER HIGHLIGHTS")
        for h in r["highlights"]:
            bullet(h)
    heading("PROFESSIONAL EXPERIENCE")
    for job in r["experience"]:
        pdf.ln(0.8)
        pdf.set_font("Helvetica", "B", 10)
        left = sanitize(f"{job['title']} | {job['company']}" + (f" | {job['location']}" if job.get("location") else ""))
        pdf.cell(w * 0.75, 5, left)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.cell(w * 0.25, 5, sanitize(job.get("dates") or ""), align="R", new_x="LMARGIN", new_y="NEXT")
        for b in job["bullets"]:
            bullet(b)
    heading("TECHNICAL SKILLS")
    for s in r["skills"]:
        pdf.set_font("Helvetica", "B", 9.5)
        label = sanitize(f"{s['category']}: ")
        pdf.cell(pdf.get_string_width(label) + 1, 4.6, label)
        pdf.set_font("Helvetica", "", 9.5)
        pdf.multi_cell(0, 4.6, sanitize(", ".join(s["items"])), align="L", new_x="LMARGIN", new_y="NEXT")
    if r.get("education"):
        heading("EDUCATION")
        for e in r["education"]:
            bullet(e)
    return bytes(pdf.output())
