*** Settings ***
Library    SSHLibrary
Library    Collections
Suite Teardown    Remove fixtures

*** Variables ***
${SCENARIO}       install
${RELEASE_IMAGE}  ghcr.io/geniusdynamics/lemonldapng:1.1.0
${LDAP_IMAGE}     ghcr.io/nethserver/openldap:2.8.1
${RUNTIME_IMAGE}  docker.io/lemonldapng/lemonldap-ng:2.23.4
${LDAP_DOMAIN}    lemon-ci.test
${HOST}           sso.lemon-ci.test
${LDAP_USER}      admin
${LDAP_PASSWORD}  LemonCI2026-Test
${PORTAL_TEST}    /root/lemonldap-test-portal.py
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
    IF    '${module_id}' != ''
        Run successfully    remove-module --no-preserve ${module_id}
    END
    IF    '${directory_id}' != ''
        Run successfully    remove-module --no-preserve ${directory_id}
    END

*** Test Cases ***
Provision the LDAP directory and portal
    Put File    ${CURDIR}/portal_login.py    ${PORTAL_TEST}
    ${id} =    Install instance    ${LDAP_IMAGE}
    Set Suite Variable    ${directory_id}    ${id}
    Run successfully    api-cli run module/${directory_id}/configure-module --data '{"provision":"new-domain","domain":"${LDAP_DOMAIN}","admuser":"${LDAP_USER}","admpass":"${LDAP_PASSWORD}"}'
    IF    '${SCENARIO}' == 'update'
        ${id} =    Install instance    ${RELEASE_IMAGE}
    ELSE
        ${id} =    Install instance    ${IMAGE_URL}
    END
    Set Suite Variable    ${module_id}    ${id}

Upgrade a configured legacy instance with LDAP, sessions and custom Nginx
    Skip If    '${SCENARIO}' != 'update'    Install scenario
    Configure portal    false
    Wait Until Keyword Succeeds    45s    3s    Running image is    docker.io/lemonldapng/lemonldap-ng:2.21.3
    Wait Until Keyword Succeeds    40s    4s    Login via Traefik    http    no
    In portal container    sh -c 'printf "# NS8 CI custom site\\n" > /etc/nginx/sites-enabled/ci-custom.conf && touch /var/lib/lemonldap-ng/sessions/ci-session /var/lib/lemonldap-ng/psessions/ci-psession'
    Run successfully    api-cli run update-module --data '{"force":true,"module_url":"${IMAGE_URL}","instances":["${module_id}"]}'
    Wait Until Keyword Succeeds    45s    3s    Running image is    ${RUNTIME_IMAGE}
    Wait Until Keyword Succeeds    40s    4s    Login via Traefik    http    no
    In portal container    sh -c 'grep -q "# NS8 CI custom site" /etc/nginx/sites-enabled/ci-custom.conf && test -f /var/lib/lemonldap-ng/sessions/ci-session && test -f /var/lib/lemonldap-ng/psessions/ci-psession'
    Run successfully    runagent -m ${module_id} sh -c 'test -n "$(find upgrade-backups -name "*.tar.gz" -print -quit)"'

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
