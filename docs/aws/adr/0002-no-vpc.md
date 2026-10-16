# ADR 2: No VPC

**Status:** accepted. **Covers:** the cost cap; reaching Google.

## Context
The runner must reach S3, DynamoDB, SSM, SQS and Google's Apps Script Web App.
A Lambda in a VPC reaches the internet only through a NAT gateway, about $33 a
month in fixed fees: 16 times the whole budget. VPC endpoints for the AWS
services cost a fixed fee each too.

## Decision
No function is attached to a VPC. They reach AWS services and Google over
Lambda's default egress. A Terraform test fails if any function gets a
`vpc_config`.

## Consequences
- No fixed network cost, and no egress that silently breaks the Web App call.
- Network isolation comes from IAM instead: every role is least-privilege and
  fenced by a permissions boundary ([ADR 7](0007-hand-applied-bootstrap-and-role-fence.md)),
  the data bucket refuses public access and plain HTTP, and the only public
  entry is the passphrase-protected page.
