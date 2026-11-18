# SPDX-License-Identifier: GPL-3.0-or-later
use strict;
use warnings;
use JSON qw(decode_json);
use Lemonldap::NG::Manager::Cli;

# The regular "restore" CLI forcibly writes a configuration. Use the normal
# Manager CLI save path with the revision supplied by the reader instead.
my $config = decode_json(do { local $/; <STDIN> });
my $cli = Lemonldap::NG::Manager::Cli->new(log => 'NS8 OIDC configuration');
$cli->app;
$cli->cfgNum($config->{cfgNum});
$cli->_save($config);
