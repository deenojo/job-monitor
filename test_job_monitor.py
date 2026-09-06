import json
import unittest

import job_monitor


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


if __name__ == "__main__":
    unittest.main()
