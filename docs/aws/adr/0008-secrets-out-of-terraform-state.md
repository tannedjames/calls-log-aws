# ADR 8: Secrets stay out of the repo, the image and Terraform's state

**Status:** accepted. **Covers:** least privilege; no secret in shared places.

## Context
The runner needs the Web App's secret and URL, the employee list and the
page's passphrase hash. Terraform state is readable by anyone who can read the
state bucket, and the usual `ignore_changes = [value]` still copies a
parameter's value into state on every refresh.

## Decision
- Terraform creates the SSM SecureString parameters with placeholders through
  the provider's write-only `value_wo`, so state holds `null`.
- A PC tool sets the real values from the PC's private files, through a
  temporary file, printing parameter names only.
- Pull-request plans run with `-refresh=false`, because a refresh decrypts
  every parameter: the plan role never needs to read one.
- The runner re-reads the parameters every 15 minutes, so a rotated secret
  reaches it without a deploy.
- CI fails closed on private data, including when the private files are absent.

## Consequences
- Rotating the Web App secret is a runbook step, checked by a probe that can't
  write to a sheet.
- The upload page's passphrase exists only as a PBKDF2 hash.
