"""Capture this app's Naukri browser session after an ordinary user sign-in."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

from src.client.naukri_client import NaukriLoginClient
from src.exceptions.exceptions import NaukriAuthError, NaukriParseError
from src.utils.config_manager import ConfigManager

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / '.browser-profile'
LOGIN_URL = 'https://www.naukri.com/nlogin/login'


def naukri_cookies(cookies):
    """Only accept Naukri's session cookies, never another site's credentials."""
    result = {}
    for cookie in cookies:
        domain = cookie.get('domain', '').lstrip('.').lower()
        if domain == 'naukri.com' or domain.endswith('.naukri.com'):
            if cookie.get('name') in ('nauk_at', 'nauk_rt') and cookie.get('value'):
                result[cookie['name']] = cookie['value']
    return result


def save_verified_session(cookies, manager=None, client_factory=None):
    """Do not replace the saved session until the actual HTTP client accepts it."""
    if not cookies.get('nauk_at'):
        raise NaukriAuthError('Browser has no Naukri access cookie yet')
    manager = manager or ConfigManager()
    factory = client_factory or NaukriLoginClient.from_browser_cookies
    client = factory(cookies['nauk_at'], cookies.get('nauk_rt', ''), warmup=False)
    try:
        client.verify_account_session()
        # Reload here so profile edits made while signing in are preserved.
        manager._config = None
        config = manager.config
        config['session'] = {**config.get('session', {}), 'nauk_at': cookies['nauk_at'],
                             'nauk_rt': cookies.get('nauk_rt', '')}
        client.persist_session(manager)
        manager.save(config)
    finally:
        client.session.close()


def saved_session_is_valid():
    manager = ConfigManager()
    cookies = manager.config.get('session', {})
    if not cookies.get('nauk_at'):
        return False
    try:
        save_verified_session(cookies, manager)
        return True
    except NaukriAuthError:
        return False


def connect_browser(timeout=300, channel='chrome'):
    try:
        from playwright.sync_api import sync_playwright, Error
    except ImportError:
        print('Browser sign-in needs Playwright. Run Finish-Setup.cmd to install dependencies.')
        return 1
    print('Opening the NaukriAssist sign-in browser. Complete login, OTP or CAPTCHA there.', flush=True)
    print('Cookies will be captured and verified automatically. No applications will be sent.', flush=True)
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(PROFILE), channel=channel, headless=False,
                no_viewport=True, accept_downloads=False,
            )
        except Error:
            print('Could not open the sign-in browser. Install Chrome (or use --channel msedge),')
            print('and close any other NaukriAssist sign-in window before trying again.')
            return 1
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=45000)
            deadline = time.monotonic() + timeout
            checked = None
            while time.monotonic() < deadline:
                if not context.pages:
                    print('Sign-in window closed. Saved settings were preserved.')
                    return 1
                cookies = naukri_cookies(context.cookies(['https://www.naukri.com/']))
                identity = (cookies.get('nauk_at'), cookies.get('nauk_rt'))
                if cookies.get('nauk_at') and identity != checked:
                    checked = identity
                    try:
                        save_verified_session(cookies)
                    except NaukriAuthError:
                        print('Session is not accepted yet. Please complete sign-in in the browser.', flush=True)
                    except NaukriParseError:
                        print('Account verification is temporarily unavailable. Try connecting again.')
                        return 1
                    else:
                        print('Naukri session verified and saved automatically. No cookie copying needed.', flush=True)
                        return 0
                # Pump browser events; do not poll account APIs for an unchanged cookie.
                page.wait_for_timeout(1000)
            print('Sign-in timed out. Run Update-Naukri-Session.cmd to try again.')
            return 1
        except Error:
            print('Browser sign-in was interrupted. Saved settings were preserved; try again.')
            return 1
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ensure-session', action='store_true', help='Reuse a verified saved session before opening the browser')
    parser.add_argument('--channel', choices=('chrome', 'msedge'), default='chrome')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.ensure_session and saved_session_is_valid():
        print('Saved Naukri account session verified. Browser sign-in is not needed.')
        return 0
    return connect_browser(timeout=args.timeout, channel=args.channel)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Sign-in cancelled. No applications submitted.')
        raise SystemExit(130)
    except Exception:
        # Do not print response bodies or exception details containing session data.
        print('Could not verify the Naukri session. Check your connection and retry browser sign-in.')
        raise SystemExit(1)
