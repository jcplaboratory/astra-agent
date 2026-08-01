#!/bin/sh
set -eu

tenant_id="${1:?usage: generate-astra-dev-certs.sh TENANT_ID ARA_ID}"
ara_id="${2:?usage: generate-astra-dev-certs.sh TENANT_ID ARA_ID}"
output="$(dirname "$0")/tls"
mkdir -p "$output"

openssl req -x509 -newkey rsa:3072 -nodes -days 365 \
  -keyout "$output/ca.key" -out "$output/ca.crt" -subj "/CN=Astra Agent Development CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "subjectKeyIdentifier=hash"
openssl req -newkey rsa:3072 -nodes \
  -keyout "$output/server.key" -out "$output/server.csr" -subj "/CN=localhost"
printf '%s\n' \
  'basicConstraints=critical,CA:FALSE' \
  'keyUsage=critical,digitalSignature,keyEncipherment' \
  'extendedKeyUsage=serverAuth' \
  'subjectAltName=DNS:localhost,IP:127.0.0.1' > "$output/server.ext"
openssl x509 -req -in "$output/server.csr" -CA "$output/ca.crt" -CAkey "$output/ca.key" \
  -CAcreateserial -days 365 -out "$output/server.crt" -extfile "$output/server.ext"
openssl req -newkey rsa:3072 -nodes \
  -keyout "$output/ara.key" -out "$output/ara.csr" \
  -subj "/OU=$tenant_id/CN=$ara_id"
printf '%s\n' \
  'basicConstraints=critical,CA:FALSE' \
  'keyUsage=critical,digitalSignature' \
  'extendedKeyUsage=clientAuth' > "$output/ara.ext"
openssl x509 -req -in "$output/ara.csr" -CA "$output/ca.crt" -CAkey "$output/ca.key" \
  -CAcreateserial -days 365 -out "$output/ara.crt" -extfile "$output/ara.ext"
chmod 600 "$output"/*.key
printf 'Development certificates generated in %s\n' "$output"
