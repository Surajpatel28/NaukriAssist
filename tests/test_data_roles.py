"""Offline regression checks: no login, paid AI calls or applications."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.client.job_classifier import JobFilterPipeline2
from src.client.job_client import NaukriJobClient
from src.models.models import Job
from src.utils.config_manager import ConfigManager


def settings():
    return SimpleNamespace(
        config={
            'profile': {'role_focus': 'data_science', 'experience_years': 1, 'tech_stack': ['Python', 'Machine Learning']},
            'ai': {'daily_apply_limit': 1}, 'search': {'experience_flexibility_years': 1},
            'session': {'nauk_at': 'test-session'}, 'questionnaire': {'skip_ai': True},
        }, veto={'titles': ['intern', 'architect'], 'companies': []}, get_ai_key=lambda: '',
    )


def job(job_id, title, experience='0-2 Yrs', tags=None):
    return Job(job_id, title, 'Example', 'Pune', experience, '', 'today', '', tags=tags or ['Python'])


class DataRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = settings()
        self.pipeline = JobFilterPipeline2(config_manager=self.cfg, cache_file=str(Path(self.temp.name) / 'cache.json'))

    def test_target_titles_survive_filters(self):
        titles = ['Data Scientist', 'Data Science Associate', 'Machine Learning Engineer', 'AI Engineer',
                  'ML Engineer', 'NLP Engineer', 'Computer Vision Engineer', 'Deep Learning Engineer',
                  'GenAI Engineer', 'LLM Engineer', 'MLOps Engineer', 'Data Engineer', 'Analytics Engineer',
                  'Data Analyst', 'Business Intelligence Analyst', 'Applied Scientist', 'BI Developer']
        result = self.pipeline.filter_candidates([job(str(i), title) for i, title in enumerate(titles)])
        self.assertEqual({j['title'] for j in result}, set(titles))

    def test_unrelated_titles_and_substring_traps_are_rejected(self):
        titles = ['Python Backend Developer', 'Node.js Developer', 'Retail Manager', 'HTML Developer',
                  'AI Sales Executive', 'Data Entry Operator', 'Frontend Engineer', 'AI Recruiter']
        self.assertEqual(self.pipeline.filter_candidates([job(str(i), t) for i, t in enumerate(titles)]), [])

    def test_generic_title_needs_domain_skills(self):
        jobs = [job('yes', 'Software Engineer', tags=['Python', 'Machine Learning']),
                job('no', 'Software Engineer', tags=['Python', 'REST APIs'])]
        self.assertEqual([j['job_id'] for j in self.pipeline.filter_candidates(jobs)], ['yes'])

    def test_saved_experience_allows_fresher_but_rejects_senior(self):
        jobs = [job('fresh', 'Data Scientist', '0 Yrs'), job('junior', 'ML Engineer', '1-3 Yrs'),
                job('senior', 'Data Scientist', '5-8 Yrs'), job('unknown_senior', 'Senior Data Scientist', 'N/A')]
        self.assertEqual({j['job_id'] for j in self.pipeline.filter_candidates(jobs)}, {'fresh', 'junior'})

    def test_experience_parsed_from_search_placeholder(self):
        client = object.__new__(NaukriJobClient)
        parsed = client._parse_job({'jobId': '1', 'title': 'Data Scientist', 'placeholders': [{'type': 'experience', 'label': '5-8 Yrs'}]})
        self.assertEqual(parsed.experience, '5-8 Yrs')

    def test_generated_prompt_uses_saved_profile(self):
        prompt = ConfigManager().generate_prompt_from_config(self.cfg.config)
        self.assertIn('Saved experience: 1 year(s)', prompt)
        self.assertIn('Data Science', prompt)
        self.assertNotIn('Node.js', prompt)
        self.assertNotIn('Will NOT do', prompt)

    def test_no_ai_run_filters_and_deduplicates_before_batch_limit(self):
        import apply_agent
        jobs = [job('old', 'Data Scientist'), job('backend', 'Python Backend Developer'),
                job('match', 'ML Engineer'), job('extra', 'Data Analyst')]
        db = Mock()
        db.has_applied.side_effect = lambda jid, company: jid == 'old'
        client = Mock()
        client.is_external_apply.return_value = False
        client.apply_job.return_value = {'jobs': [{}]}
        with patch.object(apply_agent.NaukriLoginClient, 'from_browser_cookies', return_value=Mock()), \
             patch.object(apply_agent, 'NaukriJobClient', return_value=client), \
             patch.object(apply_agent, '_fetch_jobs', return_value=jobs), \
             patch.object(apply_agent.time, 'sleep'), \
             patch('requests.post', side_effect=AssertionError('Network must not be called')):
            stats = apply_agent.run_agent(self.cfg, db)
        self.assertEqual(client.apply_job.call_count, 1)
        self.assertEqual(client.apply_job.call_args.args[0].job_id, 'match')
        self.assertEqual(stats['skipped_dup'], 1)
        self.assertEqual(stats['applied'], 1)
        self.assertEqual(stats['total'], 4)


if __name__ == '__main__':
    unittest.main()
