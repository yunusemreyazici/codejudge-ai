FROM python:3.13-slim-bookworm

ARG SANDBOX_UID=10001
ARG SANDBOX_GID=10001

RUN groupadd --gid "${SANDBOX_GID}" sandbox \
    && useradd --no-log-init --uid "${SANDBOX_UID}" --gid "${SANDBOX_GID}" \
        --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /results \
    && python -m pip install --no-cache-dir "pytest==9.1.1" \
    && python -m pip uninstall --yes setuptools wheel pip

COPY --chown=root:root entrypoint.py /opt/codejudge/entrypoint.py
COPY --chown=root:root candidate_worker.py /opt/codejudge/candidate_worker.py
COPY --chown=root:root pytest_entrypoint.py /opt/codejudge/pytest_entrypoint.py

WORKDIR /workspace
USER root

ENTRYPOINT ["python", "/opt/codejudge/entrypoint.py"]
