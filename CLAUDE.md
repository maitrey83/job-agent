# CLAUDE.md — job-agent

Project memory for Claude Code. Read before working in this repo.

## What this is

Python + Flask + Google Gemini. Given a job description and a resume, it
generates a tailored cover letter, an outreach email, and answers to
screening questions — each with a self-reported confidence — plus a
JD-vs-resume fit score. Has three interfaces: a CLI (`agent.py create-packet`),
a web UI (`app.py`, Flask, deployed on Render), and a JSON API
(`POST /api/generate-packet`) added specifically for a sibling project,
**`Applier-Engine`** (TypeScript/Playwright form-filler — usually checked out
at `../Applier-Engine`, ask if you can't find it), to call programmatically.
See `Applier-Engine/CLAUDE.md` for how that side uses this API and what it
does with the response.

`job-agent-docs.md` has the full endpoint/CLI reference; this file is about
how to work on the code, not how to run it.

## The API contract other code depends on

`POST /api/generate-packet` — **don't change this response shape without
also updating `Applier-Engine/src/ai/jobAgentClient.ts` and
`src/types/job.types.ts` (the `AIPacket`/`AIQuestionAnswer` interfaces) in the
sibling repo.** Applier-Engine's auto-submit confidence gate directly
consumes `fitScore` and each `questionAnswers[].confidence` — silently
changing the scale, defaulting behavior, or field names there breaks Applier-
Engine's safety gate, not just this repo.

```
{
  "companyName": "...",
  "coverLetter": "...",
  "fitScore": 0.0-1.0,
  "questionAnswers": [{"question": "...", "answer": "...", "confidence": 0.0-1.0}]
}
```

Both `fit_score` and each answer's `confidence` are Gemini's own
self-assessment, produced by the prompt in `create_combined_prompt()` in
`agent.py`. If you're asked to change generation quality/tone, this function
is almost certainly where the change belongs. Two things the prompt
explicitly tells Gemini not to do, on purpose — preserve this if you touch
the prompt:

- Don't inflate `fit_score` — most candidates are not a 0.9+ fit; the prompt
  asks for a realistic, discriminating score because Applier-Engine uses it
  to decide whether to spend an application at all.
- Don't inflate per-answer `confidence` — low confidence should mean the
  resume didn't have strong direct evidence for that answer. This number is
  what decides whether a human reviews the answer before it's ever submitted
  anywhere.

`process_application()` parses Gemini's JSON defensively (handles the model
returning the old dict-shaped `question_answers` instead of the new
list-of-objects shape, clamps `fit_score`/`confidence` into `[0,1]`, degrades
to a sensible error dict on unparseable JSON) — keep that defensiveness if
you touch it. Gemini output is not schema-guaranteed.

## Auth

`APPLIER_API_KEY` env var, checked against the `X-API-Key` header on
`/api/generate-packet` only (not the HTML routes). If unset, the endpoint is
open — fine for local dev, not once this is deployed publicly, since every
call spends Gemini quota. Set it in Render's environment variables and the
matching `JOB_AGENT_API_KEY` in Applier-Engine's `.env`.

## How to test

No live Gemini calls in tests — mock `agent.model.generate_content` (a
`FakeModel`/`FakeResponse` pair returning a fixed JSON string is the pattern
already used when this was last verified; there's no persisted test file for
it yet, consider adding one under a `tests/` dir if you're making further
changes here). Verify:

```bash
pip install -r requirements.txt
python3 -c "import ast; ast.parse(open('agent.py').read()); ast.parse(open('app.py').read())"  # fast syntax check
GEMINI_API_KEY=dummy python3 -c "import app; print([str(r) for r in app.app.url_map.iter_rules()])"  # import + route check
```

For a real functional check, monkeypatch `agent.model` with a fake object
whose `generate_content(prompt)` returns an object with a `.text` attribute
set to a JSON string matching the schema above, then call
`agent.process_application(...)` directly or hit `/api/generate-packet`
through `app.app.test_client()`. Assert `fit_score`/`question_answers_detailed`
come back correctly shaped and clamped — this is what actually broke during
the schema migration to add confidence scoring, so it's the part most likely
to regress.

## Known limitations / good next steps

- Uses the `google-generativeai` Python package, which Google has deprecated
  in favor of `google-genai`. Still works, but should be migrated at some
  point — check current model-name compatibility (`gemini-2.5-flash` is
  hardcoded in `agent.py`) when you do.
- `extract_text_from_file()` (PDF/DOCX/text extraction in `app.py`) is only
  wired into the HTML upload flow, not the API — Applier-Engine extracts
  resume text on its own side (`resumeText.ts`, via `pdf-parse`) and sends
  plain text over the wire instead, since it runs locally and this repo runs
  remotely and has no access to the resume file. Keep that division of
  responsibility if you touch either side.
- Deployed on Render (free tier - cold-starts after inactivity, ~30-50s on
  the first call of a day). Acceptable for Applier-Engine's daily batch use
  case; would matter if this were ever used somewhere latency-sensitive.
- There's a local uncommitted branch, `feature/question-answering-backup`,
  with further-along changes to `templates/index.html`/`results.html` and an
  untracked `test_questions.py` that predates the API work above — check
  `git status`/`git diff` before assuming a clean working tree.
