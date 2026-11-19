"""Account verification must fail before searching or submitting applications."""
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import apply_agent
from src.client.naukri_client import NaukriLoginClient
from src.exceptions.exceptions import NaukriAuthError, NaukriParseError
from tests.test_data_roles import settings


class AccountPreflightTests(unittest.TestCase):
    def client(self, status=200, data=None):
        client = object.__new__(NaukriLoginClient)
        client.session = Mock()
        client.session.get.return_value.status_code = status
        client.session.get.return_value.ok = status == 200
        client.session.get.return_value.json.return_value = data
        client._build_headers = Mock(return_value={})
        client.sync_session = Mock()
        return client

    def test_unauthorized_account_is_rejected(self):
        for status in (401, 403):
            with self.subTest(status=status):
                client = self.client(status)
                with self.assertRaises(NaukriAuthError):
                    client.verify_account_session()

    def test_authenticated_dashboard_is_accepted(self):
        client = self.client(data={'profileId': 'test-profile'})
        client.verify_account_session()
        client.sync_session.assert_called_once()

    def test_html_or_empty_payload_does_not_prove_login(self):
        for data in (None, {}, [], '<html>Login</html>'):
            with self.subTest(data=data):
                with self.assertRaises(NaukriParseError):
                    self.client(data=data).verify_account_session()
        client = self.client()
        client.session.get.return_value.json.side_effect = ValueError('not JSON')
        with self.assertRaises(NaukriParseError):
            client.verify_account_session()

    def test_failed_preflight_stops_before_fetch_and_history(self):
        client, db = Mock(), Mock()
        client.verify_account_session.side_effect = NaukriAuthError('Rejected', 401)
        with contextlib.redirect_stdout(io.StringIO()), \
             patch.object(apply_agent.NaukriLoginClient, 'from_browser_cookies', return_value=client), \
             patch.object(apply_agent, '_fetch_jobs') as fetch, \
             patch.object(apply_agent, 'NaukriJobClient') as job_client:
            with self.assertRaises(NaukriAuthError):
                apply_agent.run_agent(settings(), db)
        fetch.assert_not_called()
        job_client.assert_not_called()
        db.mark_applied.assert_not_called()
        client.persist_session.assert_called_once()
