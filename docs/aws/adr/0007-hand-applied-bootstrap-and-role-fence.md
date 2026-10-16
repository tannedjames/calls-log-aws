# ADR 7: A hand-applied bootstrap, and a fence around every function role

**Status:** accepted. **Covers:** no long-lived keys; least privilege; "the deploy role can't widen itself".

## Context
GitHub deploys through OIDC with no stored key. But a role that applies
Terraform could, through a merged commit, grant itself or a new role anything.

## Decision
- A small bootstrap module, applied only by a person after reading its plan,
  holds the state bucket, the OIDC provider, the deploy and plan roles and every
  right the deploy role has. Each right is scoped to `calls-log-*` resources and
  pinned by offline tests.
- Each role trusts one exact token subject. This repository's subjects use
  GitHub's immutable IDs, so a deleted and re-created repository can't deploy.
  The plan role, trusted by pull requests, can only read.
- Function roles must sit under `/calls-log/` with a permissions boundary that
  lists the most any function may do. The deploy role can create no other kind
  of role and pass them only to Lambda.
- Terraform refuses to run against any account but the project's.

## Consequences
- A story that adds an AWS service also adds a bootstrap change, applied by
  hand before its PR merges. That is deliberate friction.
- The first live deploys found rights the documentation hadn't predicted;
  each became one reviewed bootstrap change ([lessons](../lessons-from-the-first-deploys.md)).
