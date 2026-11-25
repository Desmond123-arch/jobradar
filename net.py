"""Making secure (https) requests work on a computer whose Python has no certificates.

The usual case is a Mac with Python from python.org: until its "Install Certificates" step is run, Python trusts no
website, so every request fails with CERTIFICATE_VERIFY_FAILED. Checking is never switched off. Instead, when Python
has no certificates of its own, this points it at a certificate list the computer already has: the one from the
`certifi` package if it is installed, otherwise the system's (macOS keeps one at /etc/ssl/cert.pem).
"""

import os
import ssl
import urllib.request

SYSTEM_BUNDLES = [
    "/etc/ssl/cert.pem",                   # macOS, and some Linux
    "/private/etc/ssl/cert.pem",           # macOS
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, WSL
    "/etc/pki/tls/certs/ca-bundle.crt",    # Fedora, Red Hat
    "/etc/ssl/ca-bundle.pem",              # openSUSE
]
MARKER = "CERTIFICATE_VERIFY_FAILED"
FIX = ("Python on this computer can't check websites' security certificates, so every web request failed. "
       "On a Mac: open Finder > Applications > Python 3.x and double-click 'Install Certificates.command'. "
       "Anywhere: run  python3 -m pip install certifi  and start Job Radar again.")


def python_has_certs(verify=None, exists=os.path.exists, isdir=os.path.isdir, listdir=os.listdir):
    verify = verify or ssl.get_default_verify_paths()
    if verify.cafile and exists(verify.cafile):
        return True
    try:
        return bool(verify.capath and isdir(verify.capath) and listdir(verify.capath))
    except OSError:
        return False


def certifi_bundle():
    try:
        import certifi
        return certifi.where()
    except Exception:
        return None


def find_bundle(env=None, verify=None, exists=os.path.exists, isdir=os.path.isdir, listdir=os.listdir, certifi_path=certifi_bundle):
    """The certificate file to use instead of Python's own, or None when Python's own will do (or nothing is available)."""
    env = os.environ if env is None else env
    if env.get("SSL_CERT_FILE") or env.get("SSL_CERT_DIR") or python_has_certs(verify, exists, isdir, listdir):
        return None
    for candidate in [certifi_path()] + SYSTEM_BUNDLES:
        if candidate and exists(candidate):
            return candidate
    return None


def install(**kw):
    """Use the fallback certificates for every request in this program. Returns the file used, or None."""
    bundle = find_bundle(**kw)
    if bundle:
        try:
            context = ssl.create_default_context(cafile=bundle)
        except (ssl.SSLError, OSError):
            return None
        urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=context)))
    return bundle


def collapse(problems):
    """Many identical certificate failures become one message saying how to fix it."""
    bad = [p for p in problems if MARKER in str(p)]
    if not bad:
        return list(problems)
    return [f"{len(bad)} requests failed. {FIX}"] + [p for p in problems if MARKER not in str(p)]
