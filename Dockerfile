FROM python:3.14.7-slim-trixie AS build

WORKDIR /app

COPY pyproject.toml README.txt LICENSE ./
COPY src ./src

RUN python -m pip wheel     --no-cache-dir     --wheel-dir /wheels     .


FROM python:3.14.7-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=build /wheels /wheels

RUN python -m pip install     --no-cache-dir     /wheels/*.whl \
    && rm -rf /wheels

ENTRYPOINT ["pcd"]