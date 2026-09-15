#!/usr/bin/env bash
# Test the public CLI without installing packages or starting services.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

for input in \
    'www.kzzou.cloud' \
    'https://www.kzzou.cloud' \
    'http://www.kzzou.cloud/' \
    '  HTTPS://WWW.KZZOU.CLOUD/  ' \
    'www.kzzou.cloud/' \
    'https://www.kzzou.cloud:443/' \
    '[https://www.kzzou.cloud](https://www.kzzou.cloud)'; do
    output=$(bash "$ROOT/install-3x-ui-caddy.sh" --render "$input")
    [[ "$output" == *$'\nhttps://www.kzzou.cloud:8443 {'* ]] || {
        printf 'FAIL: domain was not normalized: %s\n' "$input" >&2
        exit 1
    }
done

for input in \
    'https://www.kzzou.cloud:2053/' \
    'https://user:password@www.kzzou.cloud/' \
    'https://www.kzzou.cloud/path' \
    'https://www.kzzou.cloud/?query=1' \
    'https://www.kzzou.cloud/#fragment' \
    'ftp://www.kzzou.cloud/' \
    'https://www.kzzou.cloud;id' \
    'https://www..kzzou.cloud/' \
    'https://127.0.0.1/' \
    'https://www.kzzou.cloud/ another.example'; do
    if bash "$ROOT/install-3x-ui-caddy.sh" --render "$input" >/dev/null 2>&1; then
        printf 'FAIL: invalid input accepted: %s\n' "$input" >&2
        exit 1
    fi
done

printf 'PASS: 7 accepted domain/URL inputs, 10 invalid inputs rejected\n'
