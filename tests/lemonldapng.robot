*** Settings ***
Library    SSHLibrary
Library    Collections
Suite Teardown    Remove fixtures

*** Variables ***
${SCENARIO}       install
${RELEASE_IMAGE}  ghcr.io/geniusdynamics/lemonldapng:2.0.0
${LDAP_IMAGE}     ghcr.io/nethserver/openldap:2.8.1
${RUNTIME_IMAGE}  docker.io/lemonldapng/lemonldap-ng:2.23.4
${LDAP_DOMAIN}    lemon-ci.test
${HOST}           sso.lemon-ci.test
${LDAP_USER}      admin
${LDAP_PASSWORD}  LemonCI2026-Test
${PORTAL_TEST}    /root/portal_login.py
${OIDC_TEST}      /root/lemonldap-oidc-test.py
${OIDC_APP}       fakeapp1
${OIDC_MAIL}      fakemail1
${OIDC_ISSUER}    https://auth.${HOST}/ns8-realms/${LDAP_DOMAIN}
${module_id}      ${EMPTY}
${directory_id}   ${EMPTY}

*** Keywords ***
Run successfully
    [Arguments]    ${command}
    ${stdout}  ${stderr}  ${rc} =    Execute Command    ${command}
    ...    return_stderr=True    return_rc=True
    Should Be Equal As Integers    ${rc}    0    ${stdout} ${stderr}
    RETURN    ${stdout}

Install instance
    [Arguments]    ${image}
    ${result} =    Run successfully    add-module ${image} 1
    ${id} =    Evaluate    ast.literal_eval($result)['module_id']    modules=ast
    RETURN    ${id}

Configure portal
    [Arguments]    ${redirect}
    Run successfully    api-cli run module/${module_id}/configure-module --data '{"host":"${HOST}","ldap_domain":"${LDAP_DOMAIN}","http2https":${redirect},"lets_encrypt":false,"cda_status":false,"saml_status":false}'

In portal container
    [Arguments]    ${command}
    ${result} =    Run successfully    runagent -m ${module_id} podman exec lemonldapng-app ${command}
    RETURN    ${result}

Running image is
    [Arguments]    ${expected}
    ${actual} =    Run successfully    runagent -m ${module_id} podman inspect --format '{{.ImageName}}' lemonldapng-app
    Should Be Equal    ${actual}    ${expected}

Login via Traefik
    [Arguments]    ${scheme}    ${secure}
    Run successfully    python3 ${PORTAL_TEST} ${scheme} auth.${HOST} ${LDAP_USER} ${LDAP_PASSWORD} ${secure}

Remove fixtures
    Execute Command    redis-cli --no-auth-warning hdel cluster/module_domains ${OIDC_APP} ${OIDC_MAIL}
    Execute Command    rm -f /root/lemon-app.json /root/lemon-mail.json /root/lemon-app-repeat.json /root/lemon-app-rotated.json
    IF    '${module_id}' != ''
        Run successfully    remove-module --no-preserve ${module_id}
    END
    IF    '${directory_id}' != ''
        Run successfully    remove-module --no-preserve ${directory_id}
    END

*** Test Cases ***
Provision the LDAP directory and portal
    Put File    ${CURDIR}/portal_login.py    ${PORTAL_TEST}
    Put File    ${CURDIR}/oidc_login.py    ${OIDC_TEST}
    ${id} =    Install instance    ${LDAP_IMAGE}
    Set Suite Variable    ${directory_id}    ${id}
    Run successfully    api-cli run module/${directory_id}/configure-module --data '{"provision":"new-domain","domain":"${LDAP_DOMAIN}","admuser":"${LDAP_USER}","admpass":"${LDAP_PASSWORD}"}'
    IF    '${SCENARIO}' == 'update'
        ${id} =    Install instance    ${RELEASE_IMAGE}
    ELSE
        ${id} =    Install instance    ${IMAGE_URL}
    END
    Set Suite Variable    ${module_id}    ${id}

Upgrade a configured 2.0.0 instance with LDAP, sessions and custom Nginx
    Skip If    '${SCENARIO}' != 'update'    Install scenario
    Configure portal    false
    Wait Until Keyword Succeeds    45s    3s    Running image is    ${RUNTIME_IMAGE}
    Wait Until Keyword Succeeds    40s    4s    Login via Traefik    http    no
    In portal container    sh -c 'printf "# NS8 CI custom site\\n" > /etc/nginx/sites-enabled/ci-custom.conf && touch /var/lib/lemonldap-ng/sessions/ci-session /var/lib/lemonldap-ng/psessions/ci-psession'
    Run successfully    api-cli run update-module --data '{"force":true,"module_url":"${IMAGE_URL}","instances":["${module_id}"]}'
    Wait Until Keyword Succeeds    45s    3s    Running image is    ${RUNTIME_IMAGE}
    Wait Until Keyword Succeeds    40s    4s    Login via Traefik    http    no
    In portal container    sh -c 'grep -q "# NS8 CI custom site" /etc/nginx/sites-enabled/ci-custom.conf && test -f /var/lib/lemonldap-ng/sessions/ci-session && test -f /var/lib/lemonldap-ng/psessions/ci-psession'
    ${redis} =    Run successfully    redis-cli --no-auth-warning exists module/${module_id}/srv/http/oidc
    Should Be Equal As Integers    ${redis}    0

Configure Traefik HTTPS and verify a secure session cookie
    Configure portal    true
    Wait Until Keyword Succeeds    45s    3s    Running image is    ${RUNTIME_IMAGE}
    In portal container    grep -Fq "fastcgi_param HTTP_HOST" /etc/nginx/sites-enabled/portal-nginx.conf
    Wait Until Keyword Succeeds    40s    4s    Login via Traefik    https    yes
    ${out} =    Run successfully    api-cli run module/${module_id}/get-configuration
    ${config} =    Evaluate    json.loads($out)    modules=json
    Should Be Equal    ${config}[host]    ${HOST}
    Should Be Equal    ${config}[ldap_domain]    ${LDAP_DOMAIN}
    Should Be True    ${config}[http2https]
    Should Not Be True    ${config}[cda_status]
    Should Not Be True    ${config}[saml_status]

The portal stays available after restarting both services
    Run successfully    runagent -m ${module_id} systemctl --user restart lemonldapng.service lemonldapng-app.service
    Wait Until Keyword Succeeds    40s    4s    Login via Traefik    https    yes

Enable SSO only on request and publish the LDAP issuer
    ${available} =    Run successfully    redis-cli --no-auth-warning exists module/${module_id}/srv/http/oidc
    Should Be Equal As Integers    ${available}    0
    ${grant} =    Run successfully    redis-cli --no-auth-warning sismember module/${module_id}/roles/clientadm register-client
    Should Be Equal As Integers    ${grant}    1
    Run successfully    api-cli run module/${module_id}/configure-ns8-sso --data '{"enabled":true}'
    ${prefix} =    Run successfully    redis-cli --no-auth-warning hget module/${module_id}/srv/http/oidc issuer_url_prefix
    Should Be Equal    ${prefix}${LDAP_DOMAIN}    ${OIDC_ISSUER}
    ${status} =    Run successfully    curl -ks -o /dev/null -w '%{http_code}' --resolve auth.${HOST}:443:127.0.0.1 ${OIDC_ISSUER}/.well-known/openid-configuration
    Should Be Equal As Integers    ${status}    200

Register clients and sign on using the existing LDAP account
    ${failure}    ${rc} =    Execute Command    api-cli run module/${module_id}/register-client --data '{"domain":"${LDAP_DOMAIN}","module_id":"${OIDC_APP}"}'
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    2
    Should Contain    ${failure}    domain_not_bound_to_module
    Run successfully    redis-cli --no-auth-warning hset cluster/module_domains ${OIDC_APP} ${LDAP_DOMAIN} ${OIDC_MAIL} ${LDAP_DOMAIN}
    ${app_data} =    Set Variable    {"domain":"${LDAP_DOMAIN}","module_id":"${OIDC_APP}","redirect_uris":["https://app.lemon-ci.test/callback"],"web_origins":["https://app.lemon-ci.test"],"audience":["${OIDC_MAIL}"]}
    ${mail_data} =    Set Variable    {"domain":"${LDAP_DOMAIN}","module_id":"${OIDC_MAIL}"}
    Wait Until Keyword Succeeds    30s    2s    Run successfully    umask 077 && api-cli run module/${module_id}/register-client --data '${app_data}' > /root/lemon-app.json
    Run successfully    umask 077 && api-cli run module/${module_id}/register-client --data '${mail_data}' > /root/lemon-mail.json
    Run successfully    umask 077 && api-cli run module/${module_id}/register-client --data '${app_data}' > /root/lemon-app-repeat.json && cmp /root/lemon-app.json /root/lemon-app-repeat.json
    ${rotated} =    Evaluate    json.dumps({**json.loads($app_data), 'rotate_secret': True}, separators=(',', ':'))    modules=json
    Run successfully    umask 077 && api-cli run module/${module_id}/register-client --data '${rotated}' > /root/lemon-app-rotated.json && ! cmp -s /root/lemon-app.json /root/lemon-app-rotated.json
    Wait Until Keyword Succeeds    40s    4s    Run successfully    jq -n --slurpfile app /root/lemon-app-rotated.json --slurpfile mail /root/lemon-mail.json '{app:$app[0],mail:$mail[0]}' | python3 ${OIDC_TEST} auth.${HOST} ${OIDC_ISSUER} ${LDAP_USER} ${LDAP_PASSWORD}

Remove a client's registration after its module leaves the user domain
    Run successfully    redis-cli --no-auth-warning hdel cluster/module_domains ${OIDC_MAIL}
    Run successfully    redis-cli --no-auth-warning publish cluster/event/module-domain-changed '{"modules":["${OIDC_MAIL}"],"domains":["${LDAP_DOMAIN}"]}'
    Wait Until Keyword Succeeds    30s    2s    In portal container    sh -c '/usr/share/lemonldap-ng/bin/lemonldap-ng-cli save 2>/dev/null | grep -q ns8-${OIDC_APP} && ! /usr/share/lemonldap-ng/bin/lemonldap-ng-cli save 2>/dev/null | grep -q ns8-${OIDC_MAIL}'

Withdraw the provider and retain the LDAP portal
    Run successfully    api-cli run module/${module_id}/configure-ns8-sso --data '{"enabled":false}'
    ${available} =    Run successfully    redis-cli --no-auth-warning exists module/${module_id}/srv/http/oidc
    Should Be Equal As Integers    ${available}    0
    Wait Until Keyword Succeeds    40s    4s    Login via Traefik    https    yes
