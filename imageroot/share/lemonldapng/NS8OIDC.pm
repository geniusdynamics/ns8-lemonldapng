package Lemonldap::NG::Portal::Plugins::NS8OIDC;

# SPDX-License-Identifier: GPL-3.0-or-later
use strict;
use Mouse;
use JSON ();
use Lemonldap::NG::Portal::Main::Constants qw(PE_OK PE_DONE);

extends 'Lemonldap::NG::Portal::Main::Plugin';

use constant hook => {
    oidcGenerateMetadata              => 'metadata',
    oidcValidateRedirectUri           => 'validateLogout',
    oidcGenerateIntrospectionResponse => 'introspect',
    oidcGenerateTokenResponse         => 'tokenCors',
    oidcGenerateUserInfoResponse      => 'userInfoCors',
};

sub init {
    my ($self) = @_;
    for my $route (
        ['.well-known', 'openid-configuration'],
        ['oauth2', $self->conf->{oidcServiceMetaDataTokenURI}],
        ['oauth2', $self->conf->{oidcServiceMetaDataUserInfoURI}],
        ['oauth2', $self->conf->{oidcServiceMetaDataJWKSURI}],
    ) {
        for my $method (qw(addAuthRoute addUnauthRoute)) {
            $self->$method($route->[0], {$route->[1] => 'preflight'}, ['OPTIONS']);
        }
    }
    return 1;
}

sub owner {
    my ($self, $rp) = @_;
    my $option = $self->conf->{oidcRPMetaDataOptions}->{$rp} // {};
    my $client = $option->{oidcRPMetaDataOptionsClientID} // '';
    my ($comment) = split /\n/, ($option->{oidcRPMetaDataOptionsComment} // '');
    return unless $client && $rp eq "ns8-$client";
    return unless ($comment // '') eq "NS8 OIDC client for $client";
    return $client;
}

sub corsHeaders {
    my ($self, $req, $rp) = @_;
    my $origin = $req->origin // '';
    return if !$origin || $self->p->_checkSelfCors($req);

    my $clients = $self->conf->{oidcRPMetaDataOptions} // {};
    for my $key (defined $rp ? ($rp) : keys %$clients) {
        next unless $self->owner($key);
        next unless $clients->{$key}->{oidcRPMetaDataOptionsActivation} // 1;
        my (undef, $origins) = split /\n/, $clients->{$key}->{oidcRPMetaDataOptionsComment};
        if (grep { $_ eq $origin } split /\s+/, ($origins // '')) {
            push @{$req->respHeaders},
                'Access-Control-Allow-Origin' => $origin,
                'Access-Control-Allow-Methods' => 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers' => 'Authorization, Content-Type',
                'Vary' => 'Origin';
            last;
        }
    }
}

sub preflight {
    my ($self, $req) = @_;
    $self->corsHeaders($req);
    return $self->p->sendBinaryResponse($req, '', code => 204);
}

sub tokenCors {
    my ($self, $req, $rp) = @_;
    $self->corsHeaders($req, $rp);
    return PE_OK;
}

sub userInfoCors {
    my ($self, $req, $response, $rp, $session) = @_;
    if ($self->owner($rp) && defined $session->{uid}) {
        $response->{preferred_username} = $session->{uid};
    }
    # This hook also builds token claims. Emit the header only on UserInfo.
    my $path = '/oauth2/' . $self->conf->{oidcServiceMetaDataUserInfoURI};
    $self->corsHeaders($req, $rp) if $req->path_info eq $path;
    return PE_OK;
}

sub metadata {
    my ($self, $req, $metadata) = @_;
    $metadata->{ns8_sso} = JSON::true;
    $self->corsHeaders($req);
    return PE_OK;
}

sub validateLogout {
    my ($self, $req, $rp, $uri, $endpoint, $state) = @_;
    return PE_OK unless $endpoint eq 'end_session' && $self->owner($rp);
    my $options = $self->conf->{oidcRPMetaDataOptions}->{$rp};
    for my $allowed (split /\s+/, ($options->{oidcRPMetaDataOptionsPostLogoutRedirectUris} // '')) {
        if ($allowed eq $uri ||
            ($allowed =~ m{\A(https?://[^/]+/)\*\z} && index($uri, $1) == 0)) {
            $state->{result} = 1;
            return PE_DONE;
        }
    }
    $state->{result} = 0;
    return PE_DONE;
}

sub introspect {
    my ($self, $req, $response, $rp, $token) = @_;
    my $client = $self->owner($rp);
    return PE_OK unless $client;

    my $source = $token->{rp};
    my $clients = $self->conf->{oidcRPMetaDataOptions};
    unless (
        $source && $self->owner($source)
        && ($clients->{$source}->{oidcRPMetaDataOptionsActivation} // 1)
        && grep { $_ eq $client } @{$response->{aud} // []}
    ) {
        %$response = (active => JSON::false);
        return PE_OK;
    }
    $response->{preferred_username} = $response->{sub} if defined $response->{sub};
    return PE_OK;
}

1;
