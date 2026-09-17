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
        self.assertEqual(match["role"], "AI Platforms Engineer")
        self.assertTrue(match["resume"].endswith("Deen_Ojo_Resume_AI_Platforms_Engineer.pdf"))

    def test_routes_ai_product_manager_to_specific_resume(self):
        match = job_monitor.match_role("AI Product Manager Intern", self.profile)
        self.assertEqual(match["role"], "AI Product Manager")

    def test_routes_full_time_roles(self):
        match = job_monitor.match_role("Machine Learning Engineer", self.profile)
        self.assertEqual(match["role"], "AI/ML Engineer")

    def test_tags_executive_ai_roles(self):
        match = job_monitor.match_role("Chief AI Officer (CAIO)", self.profile)
        self.assertEqual(match["role"], "Chief AI Officer")
        self.assertEqual(match["tier"], "executive")
    @patch("job_monitor.request_json")
    def test_collects_listed_ashby_jobs(self, request_json):
        request_json.return_value = {"jobs": [
            {"id": "one", "title": "AI Engineer", "isListed": True, "location": "Remote", "applyUrl": "https://jobs.example/one"},
            {"id": "two", "title": "Private role", "isListed": False, "applyUrl": "https://jobs.example/two"},
        ]}
        jobs = list(job_monitor.collect_ashby("example"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "ashby")

    @patch("job_monitor.request_json")
    def test_collects_smartrecruiters_jobs(self, request_json):
        request_json.return_value = {"content": [
            {"id": "one", "name": "AI Architect", "location": {"city": "Austin", "country": "US"}, "ref": "https://jobs.example/one"}
        ]}
        jobs = list(job_monitor.collect_smartrecruiters("example"))
        self.assertEqual(jobs[0]["location"], "Austin, US")
        self.assertEqual(jobs[0]["source"], "smartrecruiters")

    @patch("job_monitor.request_json")
    def test_collects_workable_jobs(self, request_json):
        request_json.return_value = {"results": [
            {"shortcode": "one", "title": "Technical Product Manager", "location": "Remote", "url": "https://jobs.example/one"}
        ]}
        jobs = list(job_monitor.collect_workable("example"))
        self.assertEqual(jobs[0]["source"], "workable")
        self.assertEqual(jobs[0]["title"], "Technical Product Manager")

    @patch("job_monitor.request_json")
    def test_collects_workday_jobs(self, request_json):
        request_json.return_value = {"jobPostings": [
            {"title": "Growth Engineer", "externalPath": "/job/growth-engineer", "locationsText": ["Remote"], "bulletFields": ["Full-time"]}
        ]}
        jobs = list(job_monitor.collect_workday({"name": "Example", "url": "https://example.wd5.myworkdayjobs.com/wday/cxs/example/site/jobs"}))
        self.assertEqual(jobs[0]["source"], "workday")
        self.assertEqual(jobs[0]["location"], "Remote")

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
