"""Local bearer/cookie auth, bounded sessions and login limiter; no token logging."""
from __future__ import annotations

from collections import OrderedDict, deque
import hmac
import ipaddress
import os
from pathlib import Path
import secrets
import stat
import time
from urllib.parse import urlsplit

from fastapi import Request

COOKIE_NAME = "threshold_session"


def load_token(path: Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise ValueError("Token directory must not be a symlink")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    created = False
    try:
        descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "r+", encoding="ascii") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 256:
            raise ValueError("Invalid token file")
        os.fchmod(stream.fileno(), 0o600)
        token = stream.read().strip()
        if not token:
            if not created:
                raise ValueError("Existing control token file is empty; refusing to replace it")
            token = secrets.token_urlsafe(32)
            stream.seek(0)
            stream.write(token + "\n")
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        elif len(token) < 32 or len(token) > 128 or not all(c.isalnum() or c in "-_" for c in token):
            raise ValueError("Invalid existing control token")
    return token


def authority(value: str):
    if not value or any(c.isspace() or c in "/,@%\\" for c in value):
        return None
    try:
        parsed = urlsplit("//" + value)
        if parsed.username or parsed.password or not parsed.hostname:
            return None
        return parsed.hostname.lower().rstrip("."), parsed.port
    except ValueError:
        return None


def allowed_host(host_header: str, bind_host: str, allow_lan: bool) -> bool:
    parsed = authority(host_header)
    if parsed is None:
        return False
    hostname, _ = parsed
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    if not allow_lan:
        return False
    if bind_host not in ("0.0.0.0", "::"):
        return hostname == bind_host.lower()
    try:
        address = ipaddress.ip_address(hostname)
        return (address.is_private or address.is_loopback) and not address.is_unspecified and not address.is_multicast
    except ValueError:
        return False


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # CLI clients still require the secret bearer token.
    try:
        parsed = urlsplit(origin)
        host = authority(request.headers.get("host", ""))
        if parsed.scheme not in ("http", "https") or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password or host is None:
            return False
        default_port = 443 if parsed.scheme == "https" else 80
        origin_tuple = (parsed.scheme, parsed.hostname, parsed.port or default_port)
        request_tuple = (request.url.scheme, host[0], host[1] or (443 if request.url.scheme == "https" else 80))
        return origin_tuple == request_tuple
    except ValueError:
        return False


class Auth:
    def __init__(self, token_path: Path):
        self.token = load_token(token_path)
        self.sessions = OrderedDict()
        self.attempts = OrderedDict()

    def token_matches(self, candidate: str) -> bool:
        return isinstance(candidate, str) and len(candidate) <= 256 and hmac.compare_digest(candidate.encode("utf-8"), self.token.encode("ascii"))

    def authorized(self, request: Request) -> bool:
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer ") and self.token_matches(header[7:]):
            return True
        session = request.cookies.get(COOKIE_NAME)
        expiry = self.sessions.get(session)
        if expiry is None:
            return False
        if time.monotonic() > expiry:
            self.sessions.pop(session, None)
            return False
        return True

    def permit_login(self, peer: str) -> bool:
        now = time.monotonic()
        recent = self.attempts.setdefault(peer, deque(maxlen=10))
        self.attempts.move_to_end(peer)
        while len(self.attempts) > 1024:
            self.attempts.popitem(last=False)
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= 5:
            return False
        recent.append(now)
        return True

    def new_session(self) -> str:
        now = time.monotonic()
        for session, expiry in list(self.sessions.items()):
            if expiry < now:
                self.sessions.pop(session, None)
        while len(self.sessions) >= 128:
            self.sessions.popitem(last=False)
        session = secrets.token_urlsafe(32)
        self.sessions[session] = now + 12 * 3600
        return session
