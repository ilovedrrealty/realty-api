#!/bin/bash
set -euo pipefail
umask 077

[[ -d /sys/class/net/lo && $(ls /sys/class/net) == lo ]]
export PATH=/usr/lib/postgresql/17/bin:/usr/bin:/bin
state=$(mktemp -d /tmp/ildr-native-db.XXXXXXXX)
started=false
cleanup() {
  local status=$?
  trap - EXIT
  if [[ $started == true ]]; then
    pg_ctl -D "$state/data" -m immediate -w stop >/dev/null
    printf 'NATIVE_DB_RECOVERY_SYNTHETIC_CLUSTER_STOPPED\n'
  fi
  exit "$status"
}
trap cleanup EXIT
mkdir "$state/socket"

openssl req -new -x509 -newkey rsa:2048 -nodes -days 2 -subj '/CN=Synthetic Fixture Root' \
  -keyout "$state/ca.key" -out "$state/ca.crt" >/dev/null 2>&1
openssl req -new -newkey rsa:2048 -nodes -subj '/CN=127.0.0.1' \
  -keyout "$state/server.key" -out "$state/server.csr" >/dev/null 2>&1
printf 'subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' >"$state/server.conf"
openssl x509 -req -in "$state/server.csr" -CA "$state/ca.crt" -CAkey "$state/ca.key" \
  -set_serial 1 -days 2 -extfile "$state/server.conf" -out "$state/server.crt" >/dev/null 2>&1
initdb -D "$state/data" -U fixture_admin --auth-local=trust --auth-host=scram-sha-256 \
  --encoding=UTF8 --locale=C --no-sync >/dev/null
pg_ctl -D "$state/data" -l "$state/postgres.log" -w -t 30 start \
  -o "-p 5432 -h 127.0.0.1 -k $state/socket -c ssl=on -c ssl_cert_file=$state/server.crt -c ssl_key_file=$state/server.key -c shared_buffers=16MB -c max_connections=12" \
  >/dev/null
started=true
env -i /opt/fixture-venv/bin/python -I -B /fixture/run_fixture.py "$state"
