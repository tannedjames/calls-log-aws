#!/usr/bin/env bash
# Print the subject of this run's GitHub OIDC token, the value each AWS role's
# trust policy compares. With --expect-refused ROLE_ARN, also ask STS for that
# role with the token and fail unless STS refuses (AccessDenied).
#
# Needs `permissions: id-token: write`. The token itself is masked and never
# printed; only its subject claim is. Used by deploy.yml.
set -euo pipefail

# Anything but no arguments or exactly "--expect-refused ROLE_ARN" is a
# mistake, and must not quietly skip the refusal check.
if ! { [ $# -eq 0 ] || { [ $# -eq 2 ] && [ "$1" = "--expect-refused" ] && [ -n "$2" ]; }; }; then
  echo "::error::usage: oidc-check.sh [--expect-refused ROLE_ARN] (got: $*)"
  exit 2
fi

if [ -z "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]; then
  echo "::error::no OIDC token here (the job needs permissions: id-token: write)"
  exit 1
fi

token=$(curl -sSf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=sts.amazonaws.com" | jq -r .value)
echo "::add-mask::$token"

sub=$(cut -d. -f2 <<<"$token" | python3 -c '
import base64, json, sys
part = sys.stdin.read().strip()
print(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["sub"])')
echo "Token subject: $sub"

[ $# -eq 2 ] || exit 0
role=$2

# The output is shown only on refusal: on success it would hold credentials.
if out=$(aws sts assume-role-with-web-identity --role-arn "$role" \
    --role-session-name refusal-check --web-identity-token "$token" \
    --duration-seconds 900 2>&1); then
  echo "::error::STS accepted this token for $role; its trust policy is too wide"
  exit 1
fi
if ! grep -q "AccessDenied" <<<"$out"; then
  echo "::error::STS failed for another reason than a refusal: $out"
  exit 1
fi
echo "Refused, as it must be: $out"
