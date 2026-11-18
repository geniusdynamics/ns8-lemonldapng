# <img src="ui/src/assets/module_default_logo.png" width="5%" alt="LemonLDAP::NG"> NS8 LemonLDAP::NG Module

## 📦 Install

Install the module with:

```bash
add-module ghcr.io/geniusdynamics/lemonldapng:2.0.0 1
```

Sample output:

```json
{
  "module_id": "lemonldapng1",
  "image_name": "lemonldapng",
  "image_url": "ghcr.io/geniusdynamics/lemonldapng:2.0.0"
}
```

---

## 📚 Documentation

**Official docs**: [lemonldap-ng.org/documentation/latest](https://lemonldap-ng.org/documentation/latest)  
**Application Integration**: [lemonldap-ng.org/documentation/latest/applications.html](https://lemonldap-ng.org/documentation/latest/applications.html)

---

## 🌐 Portals and DNS Setup

Create DNS records pointing to your server IP, replacing `domain.com` with your configured host:

- `auth.domain.com`
- `manager.domain.com`
- `test1.domain.com`
- `test2.domain.com`
- `lemonldapng.domain.com`

- `auth`: app portal for users
- `manager`: admin portal (requires Domain Admins)

---

## 🔒 LDAP Auto Discovery

LDAP settings are auto-discovered at module start.

- `administrator` and `Domain Admins` can access the manager
- All other LDAP users can access the auth portal

---

## 🎨 Customisation

The `llng` directory stores custom themes and assets.

```bash
runagent -m lemonldapng1
cd llng
```

Available subfolders:

```
./llng/
├── apps
├── auth
├── captcha
├── menutab
├── plugins
├── register
├── template
├── theme
├── backgrounds
└── userdb
```

---

## 💾 Persistent Volumes

| Volume        | Mount Path                    |
|---------------|-------------------------------|
| `etc`         | `/etc/lemonldap-ng`           |
| `var-conf`    | `/var/lib/lemonldap-ng/conf`  |
| `var-sessions` | `/var/lib/lemonldap-ng/sessions` |
| `var-psessions` | `/var/lib/lemonldap-ng/psessions` |
| `nginx`       | `/etc/nginx/sites-enabled`    |

To access:

```bash
runagent -m lemonldapng1
podman exec -ti lemonldapng-app bash
cd /etc/lemonldap-ng
```

---

## ⚙️ Configure

Run:

```bash
api-cli run configure-module --agent module/lemonldapng1 --data - <<EOF
{
  "host": "lemonldapng.domain.com",
  "http2https": true,
  "lets_encrypt": false,
  "ldap_domain": "domain.com"
}
EOF
```

This starts and configures the module and sets up a Traefik virtual host.

---

## Upgrade to 2.0.0

This release runs LemonLDAP::NG 2.23.4. Existing instances must first
upgrade to module 1.1.0. GenForge keeps 1.1.0 available as the migration
checkpoint and NS8 offers 2.0.0 after that update.

Before upgrading, review the exact allowed logout redirect URIs for each
OpenID Connect relying party. If the portal delegates sign-in to another
OIDC provider, allow the portal’s `/oauth2/rlogoutreturn` URL at that
provider. The upgrade preserves existing relying-party settings and reports
missing logout allowlists in the service journal. Check partner names,
custom handlers and custom themes against the upstream upgrade guide.
[Upstream upgrade guide](https://lemonldap-ng.org/documentation/latest/upgrade.html)

## Upgrade from KSAT Design 1.0.1

From the KSAT Design image, switch to Genius Dynamics 1.1.0 once using the
command below. NS8 associates catalog updates with the installed image
source. The migration checkpoint keeps the existing NS8 instance and changes the
runtime from `coudot/lemonldap-ng:2.20.2` to
`lemonldapng/lemonldap-ng:2.21.3`. Schedule the restart in a maintenance
window and take an NS8 backup before updating.

```bash
api-cli run update-module --data '{
  "force": true,
  "module_url": "ghcr.io/geniusdynamics/lemonldapng:1.1.0",
  "instances": ["lemonldapng1"]
}'
```

The first start with the new image snapshots the configuration and Nginx
volumes under `state/upgrade-backups/`. Keep these archives private.
It refreshes the packaged Nginx sites and preserves custom sites and both
session volumes. The service uses the existing loopback backend port and
the configured Traefik routes. For a customized site, use the new
image's packaged template as the reference for reviewing changes.

After the upgrade, sign in via the normal portal URL and check access to
a protected application. The configure schema accepts a static example.
Supply the host and LDAP domain. Optional settings keep their current
values when omitted.

---

## 📥 Get Current Configuration

```bash
api-cli run get-configuration --agent module/lemonldapng1
```

---

## 🧹 Uninstall

```bash
remove-module --no-preserve lemonldapng1
```

---

## ✉️ Smarthost Discovery

The module uses:

- `bin/discover-smarthost` to populate `smarthost.env`
- `events/smarthost-changed/10reload_services` to reload the service on updates

---

## 🐞 Debugging

- View env vars:

```bash
runagent -m lemonldapng1 env
```

- Run agent shell:

```bash
runagent -m lemonldapng1
```

- View running containers:

```bash
podman ps
```

- Shell into container:

```bash
podman exec -ti lemonldapng-app sh
```

- Container environment:

```bash
podman exec lemonldapng-app env
```

---

## 🧪 Testing

```bash
./test-module.sh <NODE_ADDR> ghcr.io/geniusdynamics/lemonldapng:1.1.0
```
