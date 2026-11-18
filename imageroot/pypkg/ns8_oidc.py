# SPDX-License-Identifier: GPL-3.0-or-later
"""Manage the NS8 OIDC provider for the LDAP domain of this portal."""

import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import agent
import agent.tasks

REALM_PATH = "/ns8-realms/"
RP_OPTIONS = "oidcRPMetaDataOptions"
RP_PREFIX = "ns8-"
OWNER_COMMENT = "NS8 OIDC client for "
PLUGIN = "Lemonldap::NG::Portal::Plugins::NS8OIDC"
NO_REDIRECT = "https://invalid.invalid/ns8-introspection-only"
RP_MAPS = (
    RP_OPTIONS, "oidcRPMetaDataExportedVars", "oidcRPMetaDataMacros",
    "oidcRPMetaDataScopeRules", "oidcRPMetaDataOptionsExtraClaims",
)
ISSUER_SETTINGS = {
    "issuerDBOpenIDConnectActivation": 1,
    "issuerDBOpenIDConnectPath": "^/oauth2/",
    "issuerDBOpenIDConnectRule": "1",
    "oidcServiceAllowAuthorizationCodeFlow": 1,
    "oidcServiceAllowImplicitFlow": 0,
    "oidcServiceAllowHybridFlow": 0,
    "oidcServiceAllowDynamicRegistration": 0,
    "oidcServiceHideMetadata": 0,
    "oidcServiceMetaDataDisallowNoneAlg": 1,
    "corsEnabled": 0,
}
SIGNING_KEYS = (
    "oidcServicePrivateKeySig", "oidcServicePublicKeySig",
    "oidcServiceKeyTypeSig", "oidcServiceKeyIdSig",
)
CLI = [
    "podman", "exec", "lemonldapng-app",
    "/usr/share/lemonldap-ng/bin/lemonldap-ng-cli",
]


def validation(field, value, error):
    agent.set_status("validation-failed")
    json.dump([{"field": field, "parameter": field, "value": value, "error": error}], sys.stdout)
    raise SystemExit(2)


def state_path():
    return Path(os.environ["AGENT_STATE_DIR"]) / "ns8-sso.json"


@contextlib.contextmanager
def oidc_lock():
    with state_path().with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def saved_settings():
    path = state_path()
    if path.exists():
        return json.loads(path.read_text())
    return None


def save_settings(settings):
    with tempfile.NamedTemporaryFile(mode="w", dir=state_path().parent, delete=False) as file:
        try:
            json.dump(settings, file)
            file.flush()
            os.replace(file.name, state_path())
        finally:
            Path(file.name).unlink(missing_ok=True)


def active():
    return subprocess.run(
        ["systemctl", "--user", "-q", "is-active", "lemonldapng-app.service"],
    ).returncode == 0


def portal_host():
    return "auth." + os.environ["TRAEFIK_HOST"]


def issuer():
    return f"https://{portal_host()}{REALM_PATH}{os.environ['LDAP_DOMAIN']}"


def portal_config():
    result = subprocess.run(CLI + ["save"], text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise SystemExit("Could not read LemonLDAP::NG configuration")
    try:
        config = json.loads(result.stdout)
        if not isinstance(config, dict) or not str(config.get("cfgNum")).isdigit():
            raise ValueError
    except ValueError:
        raise SystemExit("Invalid LemonLDAP::NG configuration response") from None
    return config


def save_portal_config(config):
    source = (Path(os.environ["AGENT_INSTALL_DIR"]) / "share/lemonldapng/save-config.pl").read_text()
    result = subprocess.run(
        ["podman", "exec", "-i", "--user", "www-data:www-data", "lemonldapng-app", "perl", "-e", source],
        input=json.dumps(config, ensure_ascii=True), text=True, capture_output=True, timeout=60,
    )
    # LLNG versions configuration changes. Some CLI failures exit with zero.
    if result.returncode or not re.search(r"^Saved under number [0-9]+$", result.stderr, re.M):
        raise SystemExit("LemonLDAP::NG configuration was not saved. Review the Manager settings and retry.")


def signing_keys():
    private_key = subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
    ).stdout
    public_key = subprocess.run(
        ["openssl", "pkey", "-pubout"], input=private_key, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
    ).stdout
    return dict(zip(SIGNING_KEYS, (private_key, public_key, "RSA", secrets.token_hex(16))))


def route(enabled):
    instance = "oidc-" + os.environ["MODULE_ID"]
    traefik = agent.resolve_agent_id("traefik@node")
    if enabled:
        result = agent.tasks.run(
            agent_id=traefik, action="set-route",
            data={
                "instance": instance,
                "url": f"http://127.0.0.1:{os.environ['TCP_PORT']}",
                "host": portal_host(),
                "path": REALM_PATH + os.environ["LDAP_DOMAIN"],
                "strip_prefix": True,
                "http2https": True,
            },
        )
        agent.assert_exp(result["exit_code"] == 0, "Could not configure the NS8 OIDC route")
    else:
        result = agent.tasks.run(agent_id=traefik, action="delete-route", data={"instance": instance})
        agent.assert_exp(result["exit_code"] == 0, "Could not remove the NS8 OIDC route")


def announce(enabled):
    agent_id = os.environ["AGENT_ID"]
    key = f"{agent_id}/srv/http/oidc"
    rdb = agent.redis_connect(privileged=True)
    record = {
        "host": portal_host(),
        "issuer_url_prefix": f"https://{portal_host()}{REALM_PATH}",
        "domain": os.environ["LDAP_DOMAIN"],
    } if enabled else {}
    if enabled:
        if rdb.hgetall(key) == record:
            return
        rdb.hset(key, mapping=record)
    elif not rdb.delete(key):
        return
    rdb.publish(f"{agent_id}/event/service-oidc-changed", json.dumps({
        "host": "", "issuer_url_prefix": "", "domain": "",
        **record,
        "key": key, "module_id": os.environ["MODULE_ID"], "module_uuid": os.environ["MODULE_UUID"],
    }))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def wait_for_provider():
    url = f"http://127.0.0.1:{os.environ['TCP_PORT']}/.well-known/openid-configuration"
    request = Request(url, headers={"Host": portal_host(), "X-Forwarded-Proto": "https"})
    opener = build_opener(NoRedirect())
    for _ in range(15):
        try:
            with opener.open(request, timeout=2) as result:
                metadata = json.load(result)
            if metadata.get("issuer") == issuer() and metadata.get("ns8_sso") is True:
                return
        except (URLError, TimeoutError, ValueError):
            pass
        time.sleep(1)
    raise SystemExit("LemonLDAP::NG did not load the NS8 OIDC issuer")


def configured_for_domain():
    domain = os.environ.get("LDAP_DOMAIN")
    return (
        bool(domain)
        and os.environ.get("TRAEFIK_HTTP2HTTPS", "").lower() == "true"
        and domain in agent.get_bound_domain_list(agent.redis_connect())
    )


def managed_clients(config):
    return {
        key: options[f"{RP_OPTIONS}ClientID"]
        for key, options in config.get(RP_OPTIONS, {}).items()
        if options.get(f"{RP_OPTIONS}ClientID")
        and key == RP_PREFIX + options[f"{RP_OPTIONS}ClientID"]
        and (options.get(f"{RP_OPTIONS}Comment") or "").partition("\n")[0]
        == OWNER_COMMENT + options[f"{RP_OPTIONS}ClientID"]
    }


def remove_clients(config, keys):
    for key in keys:
        for section in RP_MAPS:
            config.get(section, {}).pop(key, None)


def restore_settings(config, settings):
    remove_clients(config, managed_clients(config))
    for key, value in settings["original"].items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value


def configure(enabled):
    with oidc_lock():
        settings = saved_settings()
        if not enabled and settings is None:
            return
        if not active():
            validation("enabled", enabled, "portal_not_running")

        config = portal_config()
        if not enabled:
            announce(False)
            restore_settings(config, settings)
            save_portal_config(config)
            route(False)
            state_path().unlink()
            return

        if not configured_for_domain() or config.get("authentication") not in ("LDAP", "AD"):
            validation("enabled", enabled, "https_and_ldap_required")
        if settings is None:
            if config.get("issuerDBOpenIDConnectActivation") not in (None, 0, "0", "") or config.get(RP_OPTIONS):
                validation("enabled", enabled, "existing_oidc_configuration")
            settings = {
                "issuer": issuer(),
                "original": {
                    key: config.get(key) for key in (
                        *ISSUER_SETTINGS, *SIGNING_KEYS, "oidcServiceMetaDataIssuer", "customPlugins",
                    )
                },
            }
            save_settings(settings)
        if settings["issuer"] != issuer():
            validation("enabled", enabled, "issuer_changed")

        plugins = re.split(r"[,\s]+", (config.get("customPlugins") or "").strip())
        update = {
            **ISSUER_SETTINGS,
            "customPlugins": ", ".join(dict.fromkeys(filter(None, [*plugins, PLUGIN]))),
            "oidcServiceMetaDataIssuer": issuer(),
        }
        if not (
            config.get(SIGNING_KEYS[0]) and config.get(SIGNING_KEYS[1])
            and config.get(SIGNING_KEYS[2], "RSA") == "RSA"
        ):
            update.update(signing_keys())
        if any(config.get(key) != value for key, value in update.items()):
            config.update(update)
            save_portal_config(config)
        route(True)
        wait_for_provider()
        announce(True)


def checked_uri(uri, field, *, origin_only=False, logout=False):
    # LLNG stores a whitespace-delimited list. A literal '*' is allowed only
    # as the full path of a registered post-logout origin.
    try:
        parsed = urlsplit(uri)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if (
            parsed.scheme not in ("https", "http") or not parsed.hostname
            or not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", parsed.netloc)
            or parsed.port == 0 or "\\" in uri or "#" in uri
            or re.search(r"[^\x21-\x7e]", uri)
            or (origin_only and uri != origin)
            or ("*" in uri and not (logout and uri == origin + "/*"))
        ):
            raise ValueError
    except ValueError:
        validation(field, "", "invalid_uri")
    return origin


def register(request):
    domain = request["domain"]
    user = os.environ.get("AGENT_TASK_USER", "")
    if user:
        if "module_id" in request:
            validation("module_id", request["module_id"], "module_id_not_allowed")
        if not re.fullmatch(r"module/[A-Za-z0-9_.-]+", user):
            validation("module_id", user, "caller_not_a_module")
        module_id = user.removeprefix("module/")
    elif "module_id" in request:
        module_id = request["module_id"]
    else:
        validation("module_id", "", "module_id_required")

    redirects = request.get("redirect_uris", [])
    if NO_REDIRECT in redirects:
        validation("redirect_uris", "", "invalid_uri")
    origins = {checked_uri(uri, "redirect_uris") for uri in redirects}
    logout_uris = request.get("post_logout_redirect_uris", [f"{o}/*" for o in sorted(origins)])
    for uri in logout_uris:
        checked_uri(uri, "post_logout_redirect_uris", logout=True)
    web_origins = request.get("web_origins", [])
    for origin in web_origins:
        checked_uri(origin, "web_origins", origin_only=True)

    with oidc_lock():
        if domain not in agent.get_bound_domain_list(agent.redis_connect(), module_id):
            validation("domain", domain, "domain_not_bound_to_module")
        settings = saved_settings()
        if not settings or not active() or not configured_for_domain():
            validation("domain", domain, "ns8_sso_unavailable")
        if domain != os.environ["LDAP_DOMAIN"] or settings["issuer"] != issuer():
            validation("domain", domain, "domain_not_served")
        wait_for_provider()

        key = RP_PREFIX + module_id
        config = portal_config()
        clients = config.setdefault(RP_OPTIONS, {})
        managed = managed_clients(config)
        if (key in clients and key not in managed) or any(
            name != key and options.get(f"{RP_OPTIONS}ClientID") == module_id
            for name, options in clients.items()
        ):
            validation("module_id", module_id, "client_id_already_used")
        previous = clients.get(key, {})
        secret = previous.get(f"{RP_OPTIONS}ClientSecret")
        if not secret or request.get("rotate_secret"):
            secret = secrets.token_urlsafe(32)
        options = {
            **previous,
            **{RP_OPTIONS + name: value for name, value in {
                "Activation": previous.get(f"{RP_OPTIONS}Activation", 1),
                "ClientID": module_id,
                "ClientSecret": secret,
                "Comment": OWNER_COMMENT + module_id + "\n" + " ".join(web_origins),
                # The Manager requires a URI even for a client that can only introspect.
                "RedirectUris": " ".join(redirects) or NO_REDIRECT,
                "PostLogoutRedirectUris": " ".join(logout_uris),
                "AdditionalAudiences": " ".join(request.get("audience", [])),
                "Public": 0,
                "AllowPasswordGrant": 0,
                "AllowClientCredentialsGrant": 0,
                "IDTokenSignAlg": "RS256",
                "IDTokenForceClaims": 1,
                "AccessTokenJWT": 1,
                "AccessTokenSignAlg": "RS256",
                "AccessTokenClaims": 1,
                "UserIDAttr": "uid",
                "Rule": "1" if redirects else "0",
            }.items()},
        }
        exported = {"preferred_username": "uid", "name": "cn", "email": "mail", "groups": "groups;string;always"}
        exported_vars = config.setdefault("oidcRPMetaDataExportedVars", {})
        if previous != options or exported_vars.get(key) != exported:
            clients[key] = options
            exported_vars[key] = exported
            save_portal_config(config)
        return {"client_id": module_id, "client_secret": secret, "realm": domain, "issuer": issuer()}


def reconcile(*, starting=False):
    with oidc_lock():
        settings = saved_settings()
        if not settings or (not starting and not active()):
            return
        rdb = agent.redis_connect()
        domain = os.environ.get("LDAP_DOMAIN")
        bound = configured_for_domain() and settings["issuer"] == issuer()
        if not bound:
            announce(False)
        config = portal_config()
        obsolete = [
            key for key, owner in managed_clients(config).items()
            if not bound or domain not in agent.get_bound_domain_list(rdb, owner)
        ]
        if obsolete:
            remove_clients(config, obsolete)
            save_portal_config(config)
        if bound:
            wait_for_provider()
            announce(True)
