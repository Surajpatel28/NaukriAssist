"""Regression tests for imported cookies across both HTTP backends."""
import base64
import json
import unittest
from unittest.mock import patch

from src.client.naukri_client import NaukriLoginClient
from src.client.session import restore_cookies
from src.models.models import NaukriSession
from unittest.mock import Mock


class SnapshotSession:
    def __init__(self):
        self.jar = {}
        self.calls = []

    @property
    def cookies(self):
        return self.jar.copy()

    def set_cookie(self, name, value, **options):
        self.calls.append((name, options))
        self.jar[name] = value


class CookieJar(dict):
    def set(self, name, value, **options):
        self[name] = value


class SessionRestoreTests(unittest.TestCase):
    def test_httpcloak_snapshot_is_not_used_for_assignment(self):
        session = SnapshotSession()
        restore_cookies(session, {'nauk_at': 'access', 'nauk_rt': 'refresh'})
        self.assertEqual(session.cookies, {'nauk_at': 'access', 'nauk_rt': 'refresh'})
        self.assertEqual(len(session.calls), 2)
        for _, options in session.calls:
            self.assertEqual(options, {'domain': '.naukri.com', 'path': '/', 'secure': True})

    def test_curl_cookie_jar_is_supported(self):
        class CurlSession:
            cookies = CookieJar()
        session = CurlSession()
        restore_cookies(session, {'nauk_at': 'access'})
        self.assertEqual(session.cookies['nauk_at'], 'access')

    def test_client_imports_cookies_and_keeps_browser_user_agent(self):
        payload = base64.urlsafe_b64encode(json.dumps({'useragent': 'Test Browser/156'}).encode()).decode().rstrip('=')
        token = 'header.' + payload + '.signature'
        session = SnapshotSession()
        with patch('src.client.naukri_client.build_session', return_value=session), \
             patch.object(NaukriLoginClient, 'get_form_key2', return_value='test-form'):
            client = NaukriLoginClient.from_browser_cookies(token, 'refresh')
        self.assertEqual(session.cookies['nauk_at'], token)
        self.assertEqual(session.cookies['nauk_rt'], 'refresh')
        headers = client._build_headers(auth=True)
        self.assertEqual(headers['authorization'], 'Bearer ' + token)
        self.assertEqual(headers['user-agent'], 'Test Browser/156')

    def test_broken_backend_fails_locally(self):
        class BrokenSession(SnapshotSession):
            def set_cookie(self, *args, **kwargs):
                pass
        with self.assertRaises(RuntimeError):
            restore_cookies(BrokenSession(), {'nauk_at': 'access'})

    def test_rotated_cookie_is_used_for_next_authenticated_request(self):
        session = SnapshotSession()
        restore_cookies(session, {'nauk_at': 'expired', 'nauk_rt': 'refresh'})
        client = object.__new__(NaukriLoginClient)
        client.session = session
        client.naukri_session = NaukriSession('expired', session.cookies)
        session.set_cookie('nauk_at', 'fresh')
        self.assertEqual(client._build_headers(auth=True)['authorization'], 'Bearer fresh')
        self.assertEqual(client.naukri_session.bearer_token, 'fresh')

    def test_refresh_is_saved_without_overwriting_profile_or_custom_session_fields(self):
        session = SnapshotSession()
        restore_cookies(session, {'nauk_at': 'fresh', 'nauk_rt': 'fresh-refresh'})
        client = object.__new__(NaukriLoginClient)
        client.session = session
        client.naukri_session = NaukriSession('expired', {})
        cfg = Mock()
        cfg.config = {'profile': {'role_focus': 'data_science'},
                      'session': {'nauk_at': 'expired', 'custom': 'keep'}}
        client.persist_session(cfg)
        self.assertEqual(cfg.config['session']['nauk_at'], 'fresh')
        self.assertEqual(cfg.config['session']['nauk_rt'], 'fresh-refresh')
        self.assertEqual(cfg.config['session']['custom'], 'keep')
        self.assertEqual(cfg.config['profile']['role_focus'], 'data_science')
        cfg.save.assert_called_once_with(cfg.config)

    def test_browser_import_syncs_token_after_profile_warmup(self):
        session = SnapshotSession()
        def warmup(client):
            session.set_cookie('nauk_at', 'rotated-access')
            return 'form-key'
        with patch('src.client.naukri_client.build_session', return_value=session), \
             patch.object(NaukriLoginClient, 'get_form_key2', warmup):
            client = NaukriLoginClient.from_browser_cookies('old-access', 'refresh')
        self.assertEqual(client.naukri_session.bearer_token, 'rotated-access')


if __name__ == '__main__':
    unittest.main()
