"""API contract tests for /api/generate-packet with Gemini mocked out.

Run: GEMINI_API_KEY=dummy python3 -m unittest discover -s tests -v
No live Gemini calls - agent.model is replaced with a FakeModel.
"""
import json
import os
import tempfile
import unittest

os.environ.setdefault("GEMINI_API_KEY", "dummy")

import agent  # noqa: E402
import app as app_module  # noqa: E402


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.prompts = []

    def generate_content(self, prompt, generation_config=None):
        self.prompts.append(prompt)
        return FakeResponse(self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0])


class GeneratePacketTests(unittest.TestCase):
    def setUp(self):
        self._orig_model = agent.model
        self._orig_key = app_module.API_KEY
        app_module.API_KEY = None
        self.tmp = tempfile.TemporaryDirectory()
        app_module.app.config["OUTPUT_FOLDER"] = self.tmp.name
        self.client = app_module.app.test_client()

    def tearDown(self):
        agent.model = self._orig_model
        app_module.API_KEY = self._orig_key
        self.tmp.cleanup()

    def post(self, body):
        return self.client.post("/api/generate-packet", json=body)

    def test_happy_path_shape_and_clamping(self):
        agent.model = FakeModel([json.dumps({
            "company_name": "Acme",
            "fit_score": 1.7,
            "cover_letter": "Dear team,\nI am excited.",
            "outreach_email": "Hi [Name], I build Playwright suites - open to a quick chat about the SDET role?",
            "question_answers": [
                {"question": "Years of Python?", "answer": "6", "confidence": -2},
                {"question": "Why us?", "answer": "Because.", "confidence": "high"},
            ],
        })])
        r = self.post({"jobDescription": "SDET at Acme", "resumeText": "QA engineer",
                       "unmappedQuestions": ["Years of Python?", "Why us?"]})
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(d["companyName"], "Acme")
        self.assertEqual(d["fitScore"], 1.0)  # clamped
        self.assertEqual(d["questionAnswers"][0]["confidence"], 0.0)  # clamped
        self.assertEqual(d["questionAnswers"][1]["confidence"], 0.5)  # unparseable -> default
        self.assertTrue(d["outreachNote"].startswith("Hi [Name],"))
        self.assertTrue(d["coverLetterPdfBase64"])

    def test_no_contact_prompt_asks_for_placeholder_note(self):
        fake = FakeModel([json.dumps({"company_name": "Acme", "fit_score": 0.5, "cover_letter": "x"})])
        agent.model = fake
        self.post({"jobDescription": "jd", "resumeText": "resume"})
        self.assertIn('"Hi [Name],"', fake.prompts[0])

    def test_old_dict_shaped_answers_and_null_outreach(self):
        agent.model = FakeModel([json.dumps({
            "company_name": "Acme", "fit_score": 0.4, "cover_letter": "x",
            "outreach_email": None, "question_answers": {"Q1": "A1"},
        })])
        d = self.post({"jobDescription": "jd", "resumeText": "resume", "unmappedQuestions": ["Q1"]}).get_json()
        self.assertEqual(d["questionAnswers"], [{"question": "Q1", "answer": "A1", "confidence": 0.5}])
        self.assertEqual(d["outreachNote"], "")

    def test_unparseable_twice_degrades_to_error_packet(self):
        agent.model = FakeModel(["not json", "still not json"])
        d = self.post({"jobDescription": "jd", "resumeText": "resume"}).get_json()
        self.assertEqual(d["fitScore"], 0.0)
        self.assertEqual(d["companyName"], "Error")

    def test_missing_fields_400_and_auth_401(self):
        self.assertEqual(self.post({"jobDescription": "jd"}).status_code, 400)
        app_module.API_KEY = "secret"
        self.assertEqual(self.post({"jobDescription": "jd", "resumeText": "r"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()


class CoverLetterStyleTest(unittest.TestCase):
    """The letter must read like the applicant wrote it (Nov 2026): concrete rules in the prompt, dashes tidied after."""

    def test_packet_prompt_carries_the_style_rules(self):
        import agent
        p = agent.create_combined_prompt("JD text", "Resume text", "", "")
        self.assertIn("Never use these words or phrases", p)
        self.assertIn("Do not make up facts", p)
        self.assertNotIn("Paragraph 1: State position and enthusiasm", p)
        self.assertIn("Never use these words or phrases", agent.create_cover_letter_prompt("JD", "Resume"))

    def test_tidy_removes_em_dashes_and_markdown(self):
        import agent
        out = agent.tidy_cover_letter("Dear team,\n\nI built it — and it shipped.\n\n\n\n**Best,**\nAda")
        self.assertEqual(out, "Dear team,\n\nI built it - and it shipped.\n\nBest,\nAda")
        self.assertEqual(agent.tidy_cover_letter(None), "")
