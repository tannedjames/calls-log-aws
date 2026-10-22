# syntax=docker/dockerfile:1
#
# calls-log runner image: the unchanged pipeline on AWS Lambda's Python 3.13
# base (Amazon Linux 2023). x86_64 only: paddlepaddle has no Linux aarch64
# wheel for Python 3.13. Build and checks: docs/TECHNICAL.md, "Runner image".
#
#   docker build --platform linux/amd64 -t calls-log-runner .

# The Amazon Linux 2023 release whose OS packages the image runs; only its
# /etc/system-release is read. Lambda's base lags behind AL2023's releases, so
# its packages are upgraded to this one. Each release is a fixed repository
# snapshot, so the same commit always installs the same packages (FR-25).
# Dependabot bumps both images weekly. Why: docs/TECHNICAL.md, "Runner image".
FROM public.ecr.aws/amazonlinux/amazonlinux:2023.12.20261021.0-minimal@sha256:5a0fa28a0ef6511c7e01e8b96c676a345ccdd03877cf1f24708784b86ee40832 AS al2023-release

FROM public.ecr.aws/lambda/python:3.13@sha256:e0237850eecd760896365e768bac1526ea01098e325fa590e5bc735b26d5dbb9

# OS packages. First the base's own, upgraded to that release. dnf here is
# microdnf: it has no --security, and it skips a release it can't reach with
# "Nothing to do", hence the check at the end. Then the system libraries the
# wheels expect but Amazon Linux's minimal image lacks: opencv-python /
# opencv-contrib-python load libGL and glib when cv2 imports; paddlepaddle
# loads GNU OpenMP (libgomp).
RUN --mount=type=bind,from=al2023-release,source=/etc/system-release,target=/tmp/al2023-release \
    set -e; \
    release=$(sed -n 's/^Amazon Linux release \([0-9.]*\) .*/\1/p' /tmp/al2023-release); \
    [ -n "$release" ] || { echo "No release number in amazonlinux's /etc/system-release"; exit 1; }; \
    dnf upgrade -y --releasever="$release"; \
    dnf install -y mesa-libGL glib2 libgomp; \
    dnf clean all; rm -rf /var/cache/dnf; \
    now=$(rpm -q --qf '%{VERSION}' system-release); \
    if [ "$(printf '%s\n' "$release" "$now" | sort -V | head -n 1)" != "$release" ]; then \
        echo "OS packages are at Amazon Linux $now, not $release: the upgrade didn't happen"; exit 1; \
    fi; \
    echo "OS packages are at Amazon Linux $now."

WORKDIR ${LAMBDA_TASK_ROOT}

# The exact, hash-checked packages: the same lock the PC installs from.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Fail fast, naming every missing system library at once, instead of one
# ImportError per build at the (slow) model step below.
RUN command -v ldd >/dev/null || { echo "ldd is not available"; exit 1; }; \
    site=$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])'); \
    missing=$(find "$site/paddle/base" "$site/paddle/libs" "$site/cv2" "$site"/opencv_*.libs \
                -name '*.so*' -type f -exec ldd {} \; 2>/dev/null | grep 'not found' | sort -u || true); \
    if [ -n "$missing" ]; then echo "Missing system libraries:"; echo "$missing"; exit 1; fi; \
    echo "All native libraries of paddle and OpenCV resolve."

# The models: downloaded once through the pipeline's own config, checked
# against models.sha256, baked read-only. Only what this step needs is copied
# first, so code changes below don't download the models again.
COPY models.sha256 runtime_env.py config.py ocr.py ./
COPY tools/bake_models.py tools/
RUN python3 tools/bake_models.py --bake /opt/calls-log/models

# Run time: no downloads, no connectivity probe, writes only under /tmp.
ENV PADDLE_PDX_CACHE_HOME=/tmp/paddlex \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The pipeline. .dockerignore lets only runtime files through and always
# excludes secrets_local.py, so a build on the Owner's PC can't bake it in.
COPY *.py sprs.xlsx ./
COPY tools/ tools/
COPY --chmod=0755 cloud/ cloud/

ENTRYPOINT ["/var/task/cloud/entrypoint.sh"]
# No CMD: each Lambda function sets its own handler as its command
# (infra/runner.tf, guardrails.tf, bench.tf), for example
#   ["/lambda-entrypoint.sh", "cloud.runner.handler"]
