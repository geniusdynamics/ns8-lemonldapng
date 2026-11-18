# SPDX-License-Identifier: GPL-3.0-or-later
"""Preserve operator state while refreshing packaged Nginx sites."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import tarfile
import tempfile


HOST_PARAMETER = re.compile(r"^ *fastcgi_param (?:HTTP_HOST \$host|HOST \$http_host);\n", re.M)


def render(text, host):
    # Apply the same host, proxy and FastCGI settings as the entrypoint.
    for name in ("auth", "manager", "reload"):
        text = text.replace(f"{name}.example.com", f"{name}.{host}")
    text = re.sub(r"\.example\.com", lambda _: "." + host, text, count=0)
    text = re.sub(r"^.*set_real_ip_from.*$", "  set_real_ip_from 10.0.0.0/8;", text, flags=re.M)
    text = re.sub(r"^.*real_ip_header.*$", "  real_ip_header    X-Forwarded-For;", text, flags=re.M)
    # Enforce the virtual host's selected hostname in managed FastCGI sites.
    text = HOST_PARAMETER.sub("", text)
    text = re.sub(r"^( +)include /etc/nginx/fastcgi_params;$",
                  r"\1include /etc/nginx/fastcgi_params;\n\1fastcgi_param HTTP_HOST $host;",
                  text, flags=re.M)
    socket = "unix:/var/run/llng-fastcgi-server/llng-fastcgi.sock"
    if "upstream llng_portal_upstream" in text:
        return text.replace(socket, "0.0.0.0:9000")
    return text.replace("fastcgi_pass " + socket, "fastcgi_pass 0.0.0.0:9000")


def stock_digest(source, host):
    # Normalize only the transformations the published module entrypoint
    # performs. Any operator change elsewhere makes the site custom.
    source = HOST_PARAMETER.sub("", source)
    source = source.replace(f".{host}", ".example.com")
    source = source.replace("0.0.0.0:9000", "unix:/var/run/llng-fastcgi-server/llng-fastcgi.sock")
    source = source.replace("  set_real_ip_from 10.0.0.0/8;", "  #set_real_ip_from  127.0.0.1;")
    source = source.replace("  real_ip_header    X-Forwarded-For;", "  #real_ip_header    X-Forwarded-For;")
    return sha256(source.encode()).hexdigest()


def existing_site(site, etc):
    if site.is_symlink():
        if etc is None or str(site.readlink()) != f"/etc/lemonldap-ng/{site.name}":
            return None
        return etc / site.name
    return site


def snapshot(state, paths):
    directory = state / "upgrade-backups"
    directory.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{stamp}.tar.gz"
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with tarfile.open(fileobj=temporary, mode="w:gz") as archive:
                for name, data in paths.items():
                    archive.add(data, arcname=name)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
    return path


def replace_file(path, text):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(text)
            temporary.flush()
            temporary_path.chmod(0o644)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


def check_oidc_logout(conf):
    """Check the latest file configuration without guessing external endpoints."""
    revisions = [path for path in conf.glob("lmConf-*.json")
                 if path.stem.removeprefix("lmConf-").isdigit()]
    if not revisions:
        return
    latest = max(revisions, key=lambda path: int(path.stem.removeprefix("lmConf-")))
    settings = json.loads(latest.read_text())
    for name, options in (settings.get("oidcRPMetaDataOptions") or {}).items():
        if name and isinstance(options, dict) and not options.get("oidcRPMetaDataOptionsPostLogoutRedirectUris"):
            print(f"OIDC relying party {name!r} has no allowed post-logout redirect URI. "
                  "Review its logout settings in the Manager.", file=sys.stderr)


def migrate(state, runtime, host, etc, conf, nginx, templates):
    """Stage the snapshot before changing any packaged virtual host."""
    known_file = Path(__file__).with_name("stock-lemonldap-vhosts.json")
    known = json.loads(known_file.read_text())
    fresh = conf is None
    if not fresh:
        backup = snapshot(state, {"etc": etc, "var-conf": conf, "nginx": nginx})
        print(f"LemonLDAP::NG upgrade backup: {backup}", file=sys.stderr)
        check_oidc_logout(conf)
    with tarfile.open(templates) as archive:
        for name, digests in known.items():
            site = nginx / name
            if not fresh or site.exists() or site.is_symlink():
                current = existing_site(site, etc)
                if current is None or not current.is_file() or current.is_symlink():
                    continue
                if stock_digest(current.read_text(), host) not in digests:
                    print(f"LemonLDAP::NG preserving customized Nginx site {name}", file=sys.stderr)
                    continue
            packaged = archive.extractfile(f"./{name}")
            if packaged is None:
                raise ValueError(f"The pinned image is missing its packaged Nginx site {name}")
            replace_file(site, render(packaged.read().decode(), host))

    replace_file(state / "lemonldap-volume-version", runtime + "\n")


if __name__ == "__main__":
    state, runtime, host, etc, conf, nginx, templates = sys.argv[1:]
    migrate(Path(state), runtime, host, Path(etc) if etc else None,
            Path(conf) if conf else None, Path(nginx), templates)
