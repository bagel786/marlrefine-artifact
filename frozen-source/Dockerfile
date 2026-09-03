FROM python:3.13.2-slim-bookworm@sha256:6b3223eb4d93718828223966ad316909c39813dee3ee9395204940500792b740

ARG UV_VERSION=0.11.31
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /artifact

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY .dockerignore .gitignore CITATION.cff Dockerfile LICENSE LICENSE-docs-data ./
COPY adapter_refinement_pilot.py ./
COPY src ./src
COPY tests ./tests
COPY experiments ./experiments
COPY docs ./docs
COPY deposit ./deposit
COPY container ./container
COPY manifests ./manifests

RUN uv sync --frozen

CMD ["uv", "run", "pytest", "-q"]
