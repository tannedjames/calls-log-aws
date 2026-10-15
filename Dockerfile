# syntax=docker/dockerfile:1
#
# calls-log runner image: the unchanged pipeline on AWS Lambda's Python 3.13
# base (Amazon Linux 2023). x86_64 only: paddlepaddle has no Linux aarch64
# wheel for Python 3.13. Build and checks: docs/TECHNICAL.md, "Runner image".
#
#   docker build --platform linux/amd64 -t calls-log-runner .

FROM public.ecr.aws/lambda/python:3.13@sha256:27755d02113714211d0c49872554b3659831eccfc762a5d3fe602b27f1a2151c

# System libraries the wheels expect but Amazon Linux's minimal image lacks:
# opencv-python / opencv-contrib-python load libGL and glib when cv2 imports;
# paddlepaddle loads GNU OpenMP (libgomp).
RUN dnf install -y mesa-libGL glib2 libgomp && dnf clean all && rm -rf /var/cache/dnf

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
# excludes secrets_local.py, so a build on the office PC can't bake it in.
COPY *.py sprs.xlsx ./
COPY tools/ tools/
COPY --chmod=0755 cloud/ cloud/

ENTRYPOINT ["/var/task/cloud/entrypoint.sh"]
# Epic 3 adds the Lambda handler:
#   CMD ["/lambda-entrypoint.sh", "cloud.runner.handler"]
