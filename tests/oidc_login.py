#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the published issuer through Traefik using the test LDAP account."""

import base64
import json
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

from portal_login import login, request


def check(host, issuer, user, password, app, mail):
    redirect = "https://app.lemon-ci.test/callback"
    origin = "https://app.lemon-ci.test"
    route = urlsplit(issuer).path

    def call(method, path, **kw):
        return request("https", host, method, path=path, **kw)

    status, headers, body = call("GET", route + "/.well-known/openid-configuration", Origin=origin)
    assert status == 200, f"Discovery status {status}"
    discovery = json.loads(body)
    assert discovery.get("issuer") == issuer and discovery.get("ns8_sso") is True
    assert headers.get("Access-Control-Allow-Origin") == origin
    assert "registration_endpoint" not in discovery

    def endpoint(name):
        url = discovery[name]
        assert url.startswith(issuer + "/"), f"Unexpected {name}"
        return urlsplit(url).path

    cookie = login("https", host, user, password, True)
    authorize = endpoint("authorization_endpoint")
    query = urlencode({
        "client_id": app["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": "openid profile", "state": "ns8-test-state", "nonce": "ns8-test-nonce",
        "confirm": "1",
    })
    status, headers, _ = call("GET", authorize + "?" + query, cookie=cookie)
    assert status in (302, 303), f"Authorization status {status}"
    callback = urlsplit(headers["Location"])
    assert callback.netloc == "app.lemon-ci.test" and callback.path == "/callback"
    values = parse_qs(callback.query)
    assert "code" in values, f"Authorization error {values.get('error')}"
    assert values["state"] == ["ns8-test-state"]

    def basic(client):
        credentials = f"{client['client_id']}:{client['client_secret']}"
        return "Basic " + base64.b64encode(credentials.encode()).decode()

    token_endpoint = endpoint("token_endpoint")
    status, headers, _ = call(
        "OPTIONS", token_endpoint, Origin=origin,
        **{"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"},
    )
    assert status == 204 and headers.get("Access-Control-Allow-Origin") == origin
    status, headers, _ = call("OPTIONS", token_endpoint, Origin="https://other.lemon-ci.test")
    assert status == 204 and headers.get("Access-Control-Allow-Origin") is None
    status, headers, body = call(
        "POST", token_endpoint, Authorization=basic(app), Origin=origin,
        body=urlencode({"grant_type": "authorization_code", "code": values["code"][0], "redirect_uri": redirect}),
    )
    assert status == 200, f"Token exchange status {status}"
    assert headers.get("Access-Control-Allow-Origin") == origin
    tokens = json.loads(body)
    for name in ("id_token", "access_token"):
        head, payload, _ = tokens[name].split(".")
        token_header = json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)))
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        assert token_header["alg"] == "RS256"
        assert claims["iss"] == issuer and claims["preferred_username"] == user
    status, _, body = call("GET", endpoint("jwks_uri"))
    assert status == 200 and any(key["kid"] == token_header["kid"] for key in json.loads(body)["keys"])

    status, _, body = call(
        "POST", endpoint("introspection_endpoint"), Authorization=basic(mail),
        body=urlencode({"token": tokens["access_token"]}),
    )
    assert status == 200
    result = json.loads(body)
    assert result["active"] and mail["client_id"] in result["aud"]
    assert result["preferred_username"] == user and result["iss"] == issuer

    status, _, _ = call(
        "POST", token_endpoint, Authorization=basic(mail), body="grant_type=client_credentials",
    )
    assert status == 400


if __name__ == "__main__":
    host, issuer, user, password = sys.argv[1:]
    clients = json.load(sys.stdin)
    check(host, issuer, user, password, clients["app"], clients["mail"])
