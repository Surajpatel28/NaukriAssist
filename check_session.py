"""Read-only Naukri session diagnosis. Never submits applications."""
import base64
import json
from pathlib import Path
import time
from unittest.mock import patch

from src.client.naukri_client import NaukriLoginClient
from src.utils.config_manager import ConfigManager
from src.config.constants import DASHBOARD_URL, PROFILE_URL


def expired(token):
    try:
        payload = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))).get('exp', 0) <= time.time()
    except (ValueError, IndexError):
        return None


def main():
    config = json.loads((Path(__file__).parent / 'config.json').read_text(encoding='utf-8'))
    stored = config.get('session', {})
    with patch.object(NaukriLoginClient, 'get_form_key2', return_value=''):
        client = NaukriLoginClient.from_browser_cookies(stored.get('nauk_at', ''), stored.get('nauk_rt', ''))
    print('Stored access cookie expired:', expired(stored.get('nauk_at', '')), flush=True)
    try:
        for label, url in [('profile', PROFILE_URL), ('account dashboard', DASHBOARD_URL)]:
            response = client.session.get(url, headers=client._build_headers(auth=True), timeout=10)
            current = client.session.cookies.get('nauk_at', '')
            print(label, 'HTTP', response.status_code,
                  '| cookie changed:', current != stored.get('nauk_at'),
                  '| current cookie expired:', expired(current),
                  '| bearer matches jar:', client.naukri_session.bearer_token == current, flush=True)
            client.sync_session()
            if label == 'account dashboard':
                if response.status_code != 200:
                    raise RuntimeError(f'Account verification failed: HTTP {response.status_code}')
                client.persist_session(ConfigManager())
                print('Authenticated account verified; refreshed session saved. No applications submitted.', flush=True)
    finally:
        client.session.close()


if __name__ == '__main__':
    main()
