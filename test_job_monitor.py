import json
import unittest
from unittest.mock import patch

import job_monitor
from gmail_mail import GmailConfigurationError, render_job_email, send_test_email


class JobMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with job_monitor.PROFILE_PATH.open(encoding="utf-8") as profile_file:
            cls.profile = json.load(profile_file)

    def test_routes_ai_platform_internship_to_platform_resume(self):
        match = job_monitor.match_role("Software Engineering Internship - AI Platform", self.profile)
        self.assertEqual(match["role"], "AI Platforms Engineer Intern")
        self.assertTrue(match["resume"].endswith("Deen_Ojo_Resume_AI_Platforms_Engineer.pdf"))

    def test_routes_ai_product_manager_to_specific_resume(self):
        match = job_monitor.match_role("AI Product Manager Intern", self.profile)
        self.assertEqual(match["role"], "AI Product Manager Intern")

    def test_rejects_full_time_roles(self):
        self.assertIsNone(job_monitor.match_role("Machine Learning Engineer", self.profile))

    def test_normalizes_tracking_query_from_urls(self):
        self.assertEqual(
            job_monitor.canonical_url("https://example.com/job/7/?source=board#apply"),
            "https://example.com/job/7",
        )
    def test_email_payload_uses_an_escaped_direct_application_link(self):
        message = render_job_email([{
            "title": "AI Platform Intern",
            "company": "Example & Co",
            "location": "Remote",
            "matched_role": "AI Platforms Engineer Intern",
            "resume_path": "C:\\resume.pdf",
            "url": "https://jobs.example.test/apply?id=3&source=monitor",
        }])
        self.assertIn("Example &amp; Co", message)
        self.assertIn("https://jobs.example.test/apply?id=3&amp;source=monitor", message)
        self.assertIn("Open application", message)
    def test_gmail_test_fails_closed_without_credentials(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(GmailConfigurationError):
                send_test_email()


if __name__ == "__main__":
    unittest.main()
