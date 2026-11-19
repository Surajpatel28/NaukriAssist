import unittest
from unittest.mock import Mock, patch

from src.exceptions.exceptions import NaukriAuthError
from src.utils.browser_session import naukri_cookies, save_verified_session, saved_session_is_valid


class BrowserSessionTests(unittest.TestCase):
    def test_only_naukri_session_cookies_are_imported(self):
        cookies = [
            {'domain': '.naukri.com', 'name': 'nauk_at', 'value': 'test-access'},
            {'domain': 'www.naukri.com', 'name': 'nauk_rt', 'value': 'test-refresh'},
            {'domain': 'evilnaukri.com', 'name': 'nauk_at', 'value': 'wrong'},
            {'domain': 'naukri.com.evil.test', 'name': 'nauk_at', 'value': 'wrong'},
            {'domain': '.naukri.com', 'name': 'unrelated', 'value': 'private'},
        ]
        self.assertEqual(naukri_cookies(cookies), {'nauk_at': 'test-access', 'nauk_rt': 'test-refresh'})

    def test_rejected_session_never_replaces_saved_config(self):
        manager, client = Mock(), Mock()
        client.verify_account_session.side_effect = NaukriAuthError('rejected', 401)
        with self.assertRaises(NaukriAuthError):
            save_verified_session({'nauk_at': 'test-expired'}, manager, Mock(return_value=client))
        manager.save.assert_not_called()
        client.persist_session.assert_not_called()
        client.session.close.assert_called_once()

    def test_import_preserves_profile_and_clears_old_refresh_cookie(self):
        manager, client = Mock(), Mock()
        manager.config = {'profile': {'role_focus': 'data_science'},
                          'ai': {'daily_apply_limit': 5},
                          'session': {'nauk_at': 'old', 'nauk_rt': 'old-refresh', 'custom': 'keep'}}
        factory = Mock(return_value=client)
        save_verified_session({'nauk_at': 'new'}, manager, factory)
        factory.assert_called_once_with('new', '', warmup=False)
        saved = manager.save.call_args.args[0]
        self.assertEqual(saved['profile'], {'role_focus': 'data_science'})
        self.assertEqual(saved['ai']['daily_apply_limit'], 5)
        self.assertEqual(saved['session'], {'nauk_at': 'new', 'nauk_rt': '', 'custom': 'keep'})

    def test_missing_access_cookie_does_not_open_client(self):
        factory = Mock()
        with self.assertRaises(NaukriAuthError):
            save_verified_session({}, Mock(), factory)
        factory.assert_not_called()

    def test_valid_session_reuses_http_check(self):
        with patch('src.utils.browser_session.ConfigManager') as manager, \
             patch('src.utils.browser_session.save_verified_session') as save:
            manager.return_value.config = {'session': {'nauk_at': 'saved'}}
            self.assertTrue(saved_session_is_valid())
            save.assert_called_once()

    def test_expired_session_requests_browser_login(self):
        with patch('src.utils.browser_session.ConfigManager') as manager, \
             patch('src.utils.browser_session.save_verified_session', side_effect=NaukriAuthError('expired', 401)):
            manager.return_value.config = {'session': {'nauk_at': 'expired'}}
            self.assertFalse(saved_session_is_valid())
