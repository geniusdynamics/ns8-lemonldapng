#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the supplied test portal and authenticate through local Traefik."""

from html.parser import HTMLParser
import http.client
from http.cookies import SimpleCookie
import ssl
import sys
from urllib.parse import urlencode


class HiddenInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "hidden" and attributes.get("name") in ("token", "url"):
            self.fields[attributes["name"]] = attributes.get("value", "")


def request(scheme, hostname, method, body=None, cookie="", path="/", **extra_headers):
    headers = {"Host": hostname, **extra_headers}
    if cookie:
        headers["Cookie"] = cookie
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if scheme == "https":
        # NS8's throwaway test node uses its local Traefik certificate.
        conn = http.client.HTTPSConnection("127.0.0.1", timeout=10, context=ssl._create_unverified_context())
    else:
        conn = http.client.HTTPConnection("127.0.0.1", timeout=10)
    try:
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        return response.status, response.headers, response.read().decode(errors="replace")
    finally:
        conn.close()


def cookies(headers):
    found = SimpleCookie()
    for value in headers.get_all("Set-Cookie", []):
        found.load(value)
    return found


def login(scheme, hostname, user, password, secure):
    status, headers, page = request(scheme, hostname, "GET")
    if status != 200:
        raise RuntimeError(f"Login page returned HTTP {status}")
    hidden = HiddenInputs()
    hidden.feed(page)
    initial = "; ".join(f"{key}={value.coded_value}" for key, value in cookies(headers).items())
    body = urlencode({**hidden.fields, "user": user, "password": password})
    status, headers, _ = request(scheme, hostname, "POST", body, initial)
    session = cookies(headers).get("lemonldap")
    if not session or not session.value or bool(session["secure"]) != secure:
        raise RuntimeError(f"Expected a {'secure' if secure else 'plain HTTP'} session cookie. Login returned HTTP {status}.")
    return f"lemonldap={session.coded_value}"


if __name__ == "__main__":
    scheme, hostname, username, password, secure = sys.argv[1:]
    login(scheme, hostname, username, password, secure == "yes")
