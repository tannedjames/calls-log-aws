---
title: OCR cost benchmark (synthetic screenshots)
date: 2026-10-14
author: Winston (bmad-agent-architect)
status: preliminary. Re-measure on Linux in the target AWS runtime before final cost sign-off
---

# OCR cost benchmark: 2026-10-14

**Purpose:** get a CPU-seconds-per-image figure for the AWS cost model (Step 4). This benchmark
measures cost only. It does not evaluate OCR quality; parity with production is a separate test
that uses the owner's real screenshots.

## Setup

| Item | Value |
|---|---|
| Host | Windows 11 laptop, Intel i7-13620H (6P + 4E cores, 16 threads), 15.7 GB RAM, AC power, Balanced power plan |
| Python / packages | 3.13.15; paddlepaddle 3.3.1, paddleocr 3.7.0, paddlex 3.7.2, opencv(-contrib)-python 4.10.0.84, numpy 2.3.5 |
| Models downloaded | `PP-OCRv5_mobile_rec` (16 MB), `PP-OCRv6_medium_det` (59 MB, the pipeline default in this paddleocr version), `PP-LCNet_x1_0_doc_ori` (7 MB) |
| Repo settings | unchanged `config.py`, except `paddle_cpu_threads` = thread count under test |
| Input | 12 synthetic 1080×2340 call-log screenshots (11 rows, about 26 text lines each), plus 4 "photo" versions (960×1280, rotated, on a dark background). Fictional labels, no phone numbers |
| Method | `bench.py`: one process per thread count, with `OMP_NUM_THREADS`/`MKL_NUM_THREADS` set before import. It times `preprocess` + `run_ocr` per image (wall clock and process CPU time), then parses rows and builds sessions as `main.py` does |

## Results

| Run | Engine init | Typical image (wall / CPU-s) | Slow bursts seen | Peak working set |
|---|---|---|---|---|
| Screenshots, 4 threads | 4.5 s | **11.1 s / 7.3 CPU-s** (all 12 stable) | none | 739 MB |
| Screenshots, 1 thread | 3.0 s | 9.8–11.8 s / 3.7–5.6 CPU-s (7 of 12) | 5 images at 21–104 s | 742 MB |
| Screenshots, 2 threads | 3.1 s | 7.9–12.4 s / 3.3–7.7 CPU-s (4 of 12) | 8 images at 23–66 s | 741 MB |
| Screenshots, 8 threads | 5.1 s | 8.8–11.7 s / 6.2–11.1 CPU-s (8 of 12) | 4 images at 42–45 s, 90–100 CPU-s | 742 MB |
| Photos, 2 / 4 threads | 4.3–4.5 s | 12.0 s / 6.3–6.7 CPU-s | none | 731–738 MB |

- Preprocessing costs 0.1–0.2 s per image. OCR is essentially the whole cost.
- **The slow bursts are almost certainly this machine,** not the code. They cluster in time and
  hit different images in different runs; the same image is fast in one run and slow in the
  next. Likely causes are laptop power management, thermal limits or background tasks. They
  need re-checking on the target Linux runtime.
- **Accuracy sanity check against the generator's ground truth:** screenshots matched 121 of 121
  calls; photos matched 41 of 41. There were no missing or extra rows.

## What this means for the cost model (Step 4 input)

Volume: 2,000 images/week, about 8,700/month. Planning figure: **11 s wall and 7 CPU-s per
image, with a 2× safety margin.**

| Runtime | Monthly compute estimate | Headroom |
|---|---|---|
| Lambda, 2,048 MB (≈ 1.2 vCPU) | 8,700 × 2 GB × 11 s ≈ **196,000 GB-s**, inside the 400,000 GB-s always-free allowance, so **$0** | Stays free up to about 23 s per image (2.1× slower than measured). A 40-image folder takes about 7.3 min, inside Lambda's 15-min limit with 2× margin |
| Fargate Spot, 1 vCPU / 2 GB | ≈ 27 task-hours, **≈ $0.40** (us-east-1 Spot rates), plus about $0.13 for the public IPv4 address while tasks run | No time limit per run |
| Fixed costs (either route) | ECR image (venv 831 MB + 82 MB of models; compressed size to be measured) at $0.10/GB-month; S3 for about 3.5 GB of rolling 30-day images | Probably under $0.20 together |

**Conclusion (preliminary):** the $2 cap looks achievable on either route if Linux speed is close
to the speed measured here. This must be re-measured in the real container before Step 4 closes.

## Caveats

1. Measured on Windows, not on Linux or AWS hardware. AWS vCPUs may be slower than this laptop's
   performance cores.
2. Synthetic screenshots are cleaner than real ones. Real images may produce more text boxes.
3. The package versions are **newer than production's** (see `project-context.md`: detector
   drift). Pinning to production's versions may change the speed. The docs report about 5 s per
   image on 10 cores with production's setup (`docs/TECHNICAL.md:246-250`).
4. The scripts (`gen_synth.py`, `bench.py`, `check_truth.py`) are in the session scratchpad, not
   the repo. If they become a story (synthetic dataset + benchmark harness), they are a portfolio
   deliverable.
