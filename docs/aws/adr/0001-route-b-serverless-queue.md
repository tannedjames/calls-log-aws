# ADR 1: Route B, a serverless queue

**Status:** accepted (2026-10-14). **Covers:** every functional requirement; the $2 cap.

## Context
A single Windows PC ran the OCR pipeline on daily folders of up to 40
screenshots. The move to AWS had to change nothing functionally, stay under
$2 a month, need no long-lived keys, and keep the PC as a way back.

## Options
| | A: batch box on Fargate Spot | **B: upload page, queue, Lambda runner** | C: per-image fan-out with Step Functions |
|---|---|---|---|
| Expected cost | $1.15 | **$0.39** | $1.01 |
| Requirements met | misses validation, readiness, single-flight, status | **all** | all |
| Parity risk | lowest | low | medium (order across images) |
| Spend guardrails | weak | **strong and native** | medium |
| Moving parts | ~20 | ~35 | ~60 |

## Decision
Route B. Each Ready Folder becomes one SQS message; one Lambda invocation
runs the whole Folder through the unchanged pipeline in the PC's order. The
runner is a container image that also runs on Fargate, so if Lambda's cost
per image ever exceeds the budget, the same image moves to Fargate Spot with
no code change (the documented fallback).

## Consequences
- The OCR fits inside Lambda's always-free compute at the planned volume.
- Lambda's 15-minute limit needs per-image checkpoints ([ADR 5](0005-per-image-checkpoints.md)).
- Two Runs for one person and day must never overlap ([ADR 4](0004-single-flight-lease.md)).
- Turnaround is minutes against a 2-hour target, so fan-out (C) buys nothing
  the Owner needs, and adds parity risk.
