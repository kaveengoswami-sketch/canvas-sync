"""GitHub OAuth device flow backend.

Lets a desktop app authenticate the user to GitHub without opening any
terminal window. The app shows the one-time code in its own UI, opens the
browser itself, and polls until the user finishes authorizing.

Uses the GitHub CLI's public OAuth client id (published, used by `gh`).
"""

import os
import subprocess

import requests

CLIENT_ID = "178c6fc778ccc68e1d6a"
DEFAULT_SCOPE = "repo workflow"

# Suppress the console window that would otherwise flash for gh subprocesses.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

# Directories that may contain gh / git, prepended to PATH for subprocesses.
_EXTRA_PATH_DIRS = [
    r"C:\Program Files\GitHub CLI",
    r"C:\Program Files\Git\cmd",
]


def start_device_flow(scope=DEFAULT_SCOPE):
    """Begin the flow.

    Returns dict:
        {ok:True, user_code, verification_uri, device_code, interval, expires_in}
        or {ok:False, error:str}. Network errors are caught and returned as ok:False.
    """
    try:
        resp = requests.post(
            DEVICE_CODE_URL,
            data={"client_id": CLIENT_ID, "scope": scope},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - report any failure to caller
        return {"ok": False, "error": str(exc)}

    if "error" in data:
        return {
            "ok": False,
            "error": data.get("error_description") or data.get("error"),
        }

    if "device_code" not in data or "user_code" not in data:
        return {"ok": False, "error": "unexpected response: %r" % (data,)}

    return {
        "ok": True,
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri"),
        "device_code": data["device_code"],
        "interval": data.get("interval", 5),
        "expires_in": data.get("expires_in"),
    }


def poll_device_flow(device_code):
    """Poll once for the access token.

    Returns one of:
        {status:"pending"}            (authorization_pending)
        {status:"slow_down"}          (slow_down)
        {status:"expired"}            (expired_token)
        {status:"denied"}             (access_denied)
        {status:"done", token:str}    (got access_token)
        {status:"error", error:str}   (anything else / network error)
    """
    try:
        resp = requests.post(
            ACCESS_TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "device_code": device_code,
                "grant_type": GRANT_TYPE,
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}

    if data.get("access_token"):
        return {"status": "done", "token": data["access_token"]}

    error = data.get("error")
    if error == "authorization_pending":
        return {"status": "pending"}
    if error == "slow_down":
        return {"status": "slow_down"}
    if error == "expired_token":
        return {"status": "expired"}
    if error == "access_denied":
        return {"status": "denied"}

    return {"status": "error", "error": data.get("error_description") or error or "unknown error"}


def _subprocess_env():
    """Return an environment with gh/git dirs prepended to PATH."""
    env = os.environ.copy()
    extra = [d for d in _EXTRA_PATH_DIRS if os.path.isdir(d)]
    if extra:
        env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    return env


def store_token(token):
    """Make the gh CLI and git use this token.

    Pipes the token to `gh auth login --with-token`, then runs `gh auth setup-git`.
    Returns (ok: bool, detail: str).
    """
    env = _subprocess_env()
    try:
        login = subprocess.run(
            ["gh", "auth", "login", "--with-token"],
            input=token,
            text=True,
            capture_output=True,
            env=env,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        return False, "gh CLI not found on PATH"
    except Exception as exc:  # noqa: BLE001
        return False, "gh auth login failed: %s" % exc

    if login.returncode != 0:
        return False, "gh auth login failed: %s" % (login.stderr or login.stdout or "").strip()

    try:
        setup = subprocess.run(
            ["gh", "auth", "setup-git"],
            text=True,
            capture_output=True,
            env=env,
            creationflags=_NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001
        return False, "gh auth setup-git failed: %s" % exc

    if setup.returncode != 0:
        return False, "gh auth setup-git failed: %s" % (setup.stderr or setup.stdout or "").strip()

    return True, "token stored; gh and git configured"


if __name__ == "__main__":
    result = start_device_flow()
    if result.get("ok"):
        print("user_code:", result["user_code"])
        print("verification_uri:", result["verification_uri"])
        print("ghauth start OK")
    else:
        print("start_device_flow failed:", result.get("error"))
