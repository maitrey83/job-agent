"""No-fabrication tests for resume tailoring. No live Gemini calls.

Run: GEMINI_API_KEY=dummy python3 -m unittest discover -s tests -v
"""
import base64
import json
import os
import unittest

os.environ.setdefault("GEMINI_API_KEY", "dummy")

import agent  # noqa: E402
import app as app_module  # noqa: E402
import resume_tailor as rt  # noqa: E402

RESUME = """Jane Doe
Senior SDET
Austin, TX | jane@example.com
PROFILE
Senior SDET with 8+ years building test automation and CI/CD quality gates.
CAREER HIGHLIGHTS
- Cut manual regression by 40% with a Playwright suite.
- Led a migration for 1,000+ agents with zero P1 incidents.
PROFESSIONAL EXPERIENCE
Senior SDET | Acme Corp | Austin, TX Jan 2018 - Present
- Built an API quality framework with 200+ integration tests and 98% coverage, wired into Jenkins as release gates.
- Built a JMeter performance framework that validated 90 orders/sec throughput.
QA Engineer | Beta Inc | Dallas, TX Jun 2014 - Dec 2017
- Automated UI regression with Selenium, reducing manual testing by 30%.
TECHNICAL SKILLS
- Test Automation: Playwright, Selenium, Postman
- CI/CD: Jenkins, Git
EDUCATION
- M.S., Computer Science
"""

JD = "We need an SDET with Kubernetes, Go, and Playwright experience to own API testing and release gates in CI/CD. 5+ years."

PARSED = {
    "name": "Jane Doe", "headline": "Senior SDET", "contact": "Austin, TX | jane@example.com",
    "profile": "Senior SDET with 8+ years building test automation and CI/CD quality gates.",
    "certifications": [],
    "highlights": ["Cut manual regression by 40% with a Playwright suite.", "Led a migration for 1,000+ agents with zero P1 incidents."],
    "experience": [
        {"title": "Senior SDET", "company": "Acme Corp", "location": "Austin, TX", "dates": "Jan 2018 - Present",
         "bullets": ["Built an API quality framework with 200+ integration tests and 98% coverage, wired into Jenkins as release gates.",
                     "Built a JMeter performance framework that validated 90 orders/sec throughput."]},
        {"title": "QA Engineer", "company": "Beta Inc", "location": "Dallas, TX", "dates": "Jun 2014 - Dec 2017",
         "bullets": ["Automated UI regression with Selenium, reducing manual testing by 30%."]},
    ],
    "skills": [{"category": "Test Automation", "items": ["Playwright", "Selenium", "Postman"]},
               {"category": "CI/CD", "items": ["Jenkins", "Git"]}],
    "education": ["M.S., Computer Science"],
}


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def generate_content(self, prompt, generation_config=None):
        return FakeResponse(self.payloads.pop(0))


class GuardTests(unittest.TestCase):
    src = "Built an API quality framework with 200+ integration tests and 98% coverage, wired into Jenkins as release gates."

    def test_allows_reorder_style_rewording(self):
        new = "Built an API quality framework wired into Jenkins as release gates, with 200+ integration tests and 98% coverage."
        self.assertEqual(rt.guard_rewrite(new, self.src, RESUME, JD), [])

    def test_bullet_must_keep_original_verb(self):
        new = "Wired into Jenkins as release gates, built an API quality framework with 200+ integration tests and 98% coverage."
        self.assertTrue(rt.guard_rewrite(new, self.src, RESUME, JD, same_verb=True))

    def test_blocks_new_number(self):
        self.assertTrue(any("numbers" in r for r in rt.guard_rewrite(self.src.replace("98%", "99%"), self.src, RESUME, JD)))

    def test_blocks_jd_keyword_stuffing(self):
        new = self.src.replace("Jenkins", "Jenkins and Kubernetes")
        self.assertTrue(rt.guard_rewrite(new, self.src, RESUME, JD))

    def test_blocks_new_tool_not_in_jd(self):
        new = self.src.replace("Jenkins", "Jenkins and Terraform")
        self.assertTrue(rt.guard_rewrite(new, self.src, RESUME, JD))

    def test_blocks_seniority_inflation(self):
        new = "Architected and owned the company-wide API quality strategy with 200+ integration tests and 98% coverage, wired into Jenkins."
        self.assertTrue(rt.guard_rewrite(new, self.src, RESUME, JD))


class TailorEndpointTests(unittest.TestCase):
    def setUp(self):
        self._orig = agent.model
        rt._parse_cache.clear()
        app_module.API_KEY = None
        self.client = app_module.app.test_client()

    def tearDown(self):
        agent.model = self._orig

    def post(self, payloads, **extra):
        agent.model = FakeModel([json.dumps(p) if not isinstance(p, str) else p for p in payloads])
        body = {"jobDescription": JD, "resumeText": RESUME, **extra}
        return self.client.post("/api/tailor-resume", json=body).get_json()

    def test_happy_path_keeps_facts_and_reverts_fabrication(self):
        proposal = {
            "summary": "Senior SDET with 8+ years building test automation and CI/CD quality gates, including a Playwright suite.",
            "highlight_order": ["h1", "h0"],
            "experience": [
                {"job": 0, "bullets": [
                    {"id": "e0b1", "text": "Built a JMeter performance framework on Kubernetes that validated 900 orders/sec throughput."},
                    {"id": "e0b0", "text": "Built an API quality framework wired into Jenkins as release gates, with 200+ integration tests and 98% coverage."}]},
                {"job": 1, "bullets": [{"id": "e1b0", "text": "Automated UI regression with Selenium, reducing manual testing by 30%."}]},
            ],
            "skill_order": [{"category_id": "s1", "items": ["Git", "Jenkins", "Docker"]}, {"category_id": "s0", "items": ["Playwright"]}],
        }
        body = self.post([PARSED, proposal], allowRewording=True)
        self.assertTrue(body["ok"], body)
        text = body["tailoredResumeText"]
        # Fabricated bullet reverted to the original wording; nothing invented survives.
        self.assertIn("Built a JMeter performance framework that validated 90 orders/sec throughput.", text)
        for invented in ("Kubernetes", "900", "Docker"):
            self.assertNotIn(invented, text)
        self.assertEqual(len(body["changes"]["reverted"]), 1)
        # Reordering applied; facts copied verbatim; no skill dropped.
        self.assertLess(text.index("Led a migration"), text.index("Cut manual regression"))
        for fact in ("Acme Corp", "Jan 2018 - Present", "Beta Inc", "Jun 2014 - Dec 2017", "M.S., Computer Science", "Postman", "Selenium"):
            self.assertIn(fact, text)
        self.assertTrue(base64.b64decode(body["tailoredResumePdfBase64"]).startswith(b"%PDF"))

    def test_non_verbatim_parse_fails_closed(self):
        bad = json.loads(json.dumps(PARSED))
        bad["experience"][0]["title"] = "Staff SDET"  # parse invented a promotion
        body = self.post([bad, {}])
        self.assertFalse(body["ok"])
        self.assertIn("not verbatim", body["error"])

    def test_garbage_model_output_fails_closed(self):
        body = self.post(["not json", "still not", "nope"])
        self.assertFalse(body["ok"])

    def test_retries_one_malformed_response(self):
        body = self.post(["{bad json", PARSED, {"summary": "", "experience": []}])
        self.assertTrue(body["ok"], body)

    def test_summary_with_invented_number_is_rejected(self):
        proposal = {"summary": "Senior SDET with 15+ years of test automation.", "experience": []}
        body = self.post([PARSED, proposal], allowRewording=True)
        self.assertTrue(body["ok"])
        self.assertIn(PARSED["profile"], body["tailoredResumeText"])


def make_docx():
    """Shaped like the real resume: list-style bullets, TAB before dates, and a
    highlight sentence repeated verbatim under its job."""
    import io
    from docx import Document
    d = Document()
    for line in RESUME.replace("- Automated UI regression", "- Cut manual regression by 40% with a Playwright suite.\n- Automated UI regression").strip().splitlines():
        if line.startswith("- "):
            d.add_paragraph(line[2:], style="List Bullet")
        elif "|" in line and ("Present" in line or "2017" in line):
            left, dates = line.rsplit(" ", 4)[0], " ".join(line.rsplit(" ", 4)[1:])
            d.add_paragraph(f"{left}\t{dates}")
        else:
            d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


DOCX_PARSED = json.loads(json.dumps(PARSED))
DOCX_PARSED["experience"][0]["location"] = "Austin, TX"
DOCX_PARSED["experience"][0]["dates"] = "Jan 2018 - Present"
DOCX_PARSED["experience"][1]["bullets"] = ["Cut manual regression by 40% with a Playwright suite.", "Automated UI regression with Selenium, reducing manual testing by 30%."]


class DocxParseTests(unittest.TestCase):
    def test_exact_structure_from_docx(self):
        p = rt.parse_docx(make_docx())
        self.assertEqual([j["company"] for j in p["experience"]], ["Acme Corp", "Beta Inc"])
        self.assertEqual(p["experience"][0]["dates"], "Jan 2018 - Present")
        self.assertEqual(len(p["experience"][1]["bullets"]), 2)
        self.assertEqual(p["skills"][1]["raw"], "CI/CD: Jenkins, Git")

    def test_ai_parse_that_truncates_a_bullet_is_rejected(self):
        bad = json.loads(json.dumps(PARSED))
        bad["experience"][0]["bullets"][1] = "Built a JMeter performance framework"  # truncated
        agent.model = FakeModel([json.dumps(bad)])
        rt._parse_cache.clear()
        with self.assertRaises(ValueError):
            rt.parse_resume(RESUME, agent.generate_content)


class MatchingTests(unittest.TestCase):
    def test_word_variants_and_multiword_terms(self):
        text = "led code reviews, mentoring junior engineers; built ui and api automation frameworks for test"
        self.assertTrue(rt._term_in("Mentorship", text))
        self.assertTrue(rt._term_in("UI and API test automation frameworks", text))
        self.assertFalse(rt._term_in("Security testing", text))
        self.assertFalse(rt._term_in("Go", "going to google"))


class ReorderOnlyAndDocxTests(TailorEndpointTests):
    proposal = {
        "summary": "Invented summary with 20+ years.",
        "highlight_order": ["h1", "h0"],
        "experience": [{"job": 0, "bullets": [
            {"id": "e0b1", "text": "Built a JMeter performance framework on Kubernetes."},
            {"id": "e0b0", "text": "whatever"}]},
            {"job": 1, "bullets": [{"id": "e1b1", "text": ""}, {"id": "e1b0", "text": ""}]}],
        "skill_order": [{"category_id": "s1", "items": ["Git"]}],
        "requirements": ["Playwright", "Kubernetes", "CI/CD", "API testing", "Go"],
    }

    def test_default_is_reorder_only_no_sentence_changes(self):
        body = self.post([PARSED, self.proposal])
        self.assertTrue(body["ok"], body)
        text = body["tailoredResumeText"]
        self.assertIn(PARSED["profile"], text)
        self.assertNotIn("Kubernetes", text)
        self.assertEqual(body["changes"]["reworded"], [])
        self.assertLess(text.index("Built a JMeter"), text.index("Built an API quality"))

    def test_ats_report_is_literal_and_lists_gaps(self):
        report = self.post([PARSED, self.proposal])["atsReport"]
        self.assertEqual(sorted(report["missing"]), ["Go", "Kubernetes"])
        self.assertEqual(report["matchRate"], 0.6)
        where = {m["term"]: m["where"] for m in report["matched"]}
        self.assertIn("skills", where["Playwright"])

    def test_docx_reordered_with_identical_paragraphs(self):
        import io
        from collections import Counter
        from docx import Document
        original = make_docx()
        # No parse call: the .docx structure is read exactly, so the model is only asked to tailor.
        body = self.post([self.proposal], resumeText="", resumeDocxBase64=base64.b64encode(original).decode())
        self.assertTrue(body["ok"], body)
        out = Document(io.BytesIO(base64.b64decode(body["tailoredResumeDocxBase64"])))
        paras = [p.text for p in out.paragraphs if p.text.strip()]
        self.assertEqual(Counter(paras), Counter(p.text for p in Document(io.BytesIO(original)).paragraphs if p.text.strip()))
        self.assertLess(paras.index("Led a migration for 1,000+ agents with zero P1 incidents."),
                        paras.index("Cut manual regression by 40% with a Playwright suite."))
        self.assertLess(paras.index("Built a JMeter performance framework that validated 90 orders/sec throughput."),
                        paras.index("Built an API quality framework with 200+ integration tests and 98% coverage, wired into Jenkins as release gates."))
        self.assertLess(paras.index("CI/CD: Jenkins, Git"), paras.index("Test Automation: Playwright, Selenium, Postman"))
        # The repeated sentence moved inside the Beta Inc section, not at the highlight.
        beta = next(i for i, t in enumerate(paras) if t.startswith("QA Engineer | Beta Inc"))
        self.assertEqual(paras[beta + 1], "Automated UI regression with Selenium, reducing manual testing by 30%.")
        self.assertTrue(all("reordered" in n and "kept" not in n for n in body["docxNotes"]), body["docxNotes"])


if __name__ == "__main__":
    unittest.main()
