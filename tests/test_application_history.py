"""Verify persistent duplicate prevention with a real temporary SQLite DB."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import apply_agent
from src.utils.dbhandler import AppliedJobsDB
from src.exceptions.exceptions import NaukriAuthError
from tests.test_data_roles import job, settings


class ApplicationHistoryTests(unittest.TestCase):
    def test_rejected_session_stops_before_apply_and_does_not_mark_history(self):
        client = Mock()
        client.is_external_apply.side_effect = NaukriAuthError('Invalid authentication header')
        db = Mock()
        db.has_applied.return_value = False
        with contextlib.redirect_stdout(io.StringIO()), \
             patch.object(apply_agent.NaukriLoginClient, 'from_browser_cookies', return_value=Mock()), \
             patch.object(apply_agent, 'NaukriJobClient', return_value=client), \
             patch.object(apply_agent, '_fetch_jobs', return_value=[job('1', 'Data Scientist'), job('2', 'ML Engineer')]), \
             patch('requests.post', side_effect=AssertionError('No network in tests')):
            with self.assertRaises(NaukriAuthError):
                apply_agent.run_agent(settings(), db)
        self.assertEqual(client.is_external_apply.call_count, 1)
        client.apply_job.assert_not_called()
        db.mark_applied.assert_not_called()

    def test_second_run_never_submits_saved_job_even_if_company_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = str(Path(temp) / 'history.db')
            csv_path = str(Path(temp) / 'history.csv')
            first_db = AppliedJobsDB(db_path, csv_path)
            candidate = job('same-job-id', 'Data Scientist')
            client = Mock()
            client.is_external_apply.return_value = False
            client.apply_job.return_value = {'jobs': [{}]}
            with contextlib.redirect_stdout(io.StringIO()), \
                 patch.object(apply_agent.NaukriLoginClient, 'from_browser_cookies', return_value=Mock()), \
                 patch.object(apply_agent, 'NaukriJobClient', return_value=client), \
                 patch.object(apply_agent, '_fetch_jobs', return_value=[candidate]), \
                 patch.object(apply_agent.time, 'sleep'), \
                 patch('requests.post', side_effect=AssertionError('No network in tests')):
                first = apply_agent.run_agent(settings(), first_db)
                self.assertEqual(first['applied'], 1)
                self.assertEqual(client.apply_job.call_count, 1)
                # A fresh database instance simulates closing/reopening the app.
                reopened_db = AppliedJobsDB(db_path, csv_path)
                candidate.company = 'Example renamed'
                second = apply_agent.run_agent(settings(), reopened_db)
                self.assertEqual(second['applied'], 0)
                self.assertEqual(second['skipped_dup'], 1)
                self.assertEqual(client.apply_job.call_count, 1)
                self.assertEqual(reopened_db.total(), 1)

    def test_default_history_location_is_project_folder(self):
        # Inspect defaults without creating/modifying the user's actual history.
        import inspect
        parameters = inspect.signature(AppliedJobsDB).parameters
        root = Path(apply_agent.__file__).resolve().parent
        self.assertEqual(Path(parameters['db_path'].default), root / 'applied_jobs.db')
        self.assertEqual(Path(parameters['csv_path'].default), root / 'applied_jobs.csv')


if __name__ == '__main__':
    unittest.main()
