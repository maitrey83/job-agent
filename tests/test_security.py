"""Access control and input handling: the deployed service must only answer its owner,
and caller-supplied text must never be treated as a server file path or URL.

Run: GEMINI_API_KEY=dummy python3 -m unittest discover -s tests -v
"""
import base64
import json
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("GEMINI_API_KEY", "dummy")

import agent  # noqa: E402
import app as app_module  # noqa: E402

PACKET = json.dumps({"company_name": "Acme", "fit_score": 0.6,
                     "cover_letter": "Dear Acme team,\n\nI built things.\n\nBest,\nA", "question_answers": []})


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self):
        self.prompts = []

    def generate_content(self, prompt, generation_config=None):
        self.prompts.append(prompt)
        return FakeResponse(PACKET)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self._saved = (agent.model, app_module.API_KEY, app_module.WEB_PASSWORD, app_module.ON_RENDER)
        agent.model = self.model = FakeModel()
        app_module.API_KEY = "k" * 64
        app_module.WEB_PASSWORD = None
        app_module.ON_RENDER = False
        self.tmp = tempfile.TemporaryDirectory()
        app_module.app.config["OUTPUT_FOLDER"] = self.tmp.name
        self.client = app_module.app.test_client()
        self.body = {"jobDescription": "SDET at Acme", "resumeText": "QA engineer"}

    def tearDown(self):
        agent.model, app_module.API_KEY, app_module.WEB_PASSWORD, app_module.ON_RENDER = self._saved
        self.tmp.cleanup()

    # --- API key ---
    def test_api_rejects_missing_and_wrong_key(self):
        for route in ("/api/generate-packet", "/api/tailor-resume"):
            self.assertEqual(self.client.post(route, json=self.body).status_code, 401, route)
            self.assertEqual(self.client.post(route, json=self.body, headers={"X-API-Key": "nope"}).status_code, 401, route)
        self.assertEqual(self.model.prompts, [], "no Gemini call without the key")

    def test_api_accepts_right_key(self):
        r = self.client.post("/api/generate-packet", json=self.body, headers={"X-API-Key": "k" * 64})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["companyName"], "Acme")

    def test_render_without_key_refuses_everything(self):
        app_module.API_KEY = None
        app_module.ON_RENDER = True
        self.assertEqual(self.client.post("/api/generate-packet", json=self.body).status_code, 503)
        self.assertEqual(self.client.get("/").status_code, 503)
        self.assertEqual(self.model.prompts, [])

    def test_local_dev_without_key_stays_open(self):
        app_module.API_KEY = None
        self.assertEqual(self.client.post("/api/generate-packet", json=self.body).status_code, 200)

    # --- HTML pages ---
    def test_pages_need_login(self):
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertIn("Basic", self.client.get("/").headers.get("WWW-Authenticate", ""))
        bad = base64.b64encode(b"me:wrong").decode()
        self.assertEqual(self.client.get("/", headers={"Authorization": f"Basic {bad}"}).status_code, 401)
        good = base64.b64encode(("me:" + "k" * 64).encode()).decode()
        self.assertEqual(self.client.get("/", headers={"Authorization": f"Basic {good}"}).status_code, 200)
        self.assertEqual(self.client.get("/download_tmp/cover_letter_x.pdf").status_code, 401)

    def test_separate_web_password(self):
        app_module.WEB_PASSWORD = "pw"
        good = base64.b64encode(b"me:pw").decode()
        self.assertEqual(self.client.get("/", headers={"Authorization": f"Basic {good}"}).status_code, 200)

    def test_download_only_serves_cover_letter_pdfs(self):
        good = base64.b64encode(("me:" + "k" * 64).encode()).decode()
        r = self.client.get("/download_tmp/secrets.txt", headers={"Authorization": f"Basic {good}"})
        self.assertEqual(r.status_code, 404)

    # --- caller text is text ---
    def test_api_never_reads_server_files(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("SECRET_MARKER_123")
        try:
            body = {"jobDescription": f.name, "resumeText": f.name}
            r = self.client.post("/api/generate-packet", json=body, headers={"X-API-Key": "k" * 64})
            self.assertEqual(r.status_code, 200)
            self.assertNotIn("SECRET_MARKER_123", self.model.prompts[0])
            self.assertIn(f.name, self.model.prompts[0])
        finally:
            os.unlink(f.name)

    def test_api_never_fetches_urls(self):
        with mock.patch.object(agent.requests, "get") as get:
            body = {"jobDescription": "http://169.254.169.254/latest/meta-data/", "resumeText": "QA engineer"}
            r = self.client.post("/api/generate-packet", json=body, headers={"X-API-Key": "k" * 64})
            self.assertEqual(r.status_code, 200)
            get.assert_not_called()

    def test_cli_mode_still_reads_files(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("JD_FROM_FILE")
        try:
            self.assertEqual(agent.fetch_job_description(f.name), "JD_FROM_FILE")
            self.assertEqual(agent.fetch_job_description(f.name, allow_urls=False, allow_files=False), f.name)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
