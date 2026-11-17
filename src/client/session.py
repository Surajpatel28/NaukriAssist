# Default backend is httpcloak. On some Windows setups it fails with a
# permission error during session init. If you hit that, switch to
# curl_cffi by changing the line below.

USE_CURL_CFFI = False # set True if httpcloak throws a permission error


def restore_cookies(session, cookies):
    """Install cookies in the backend's real jar, scoped to Naukri HTTPS."""
    for name, value in cookies.items():
        if callable(getattr(session, 'set_cookie', None)):
            # httpcloak.cookies is a read-only snapshot, not a mutable jar.
            session.set_cookie(name, value, domain='.naukri.com', path='/', secure=True)
        else:
            session.cookies.set(name, value, domain='.naukri.com', path='/', secure=True)
        if session.cookies.get(name) != value:
            raise RuntimeError(f'Could not restore {name} into the HTTP session cookie jar')


def build_session():
    if USE_CURL_CFFI:
        from curl_cffi import requests
        return requests.Session(impersonate="chrome124", timeout=30)

    import httpcloak
    return httpcloak.Session(preset="chrome-latest", timeout=30)
