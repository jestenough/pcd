.PHONY: \
	lock \
	install \
	format \
	format-check \
	lint \
	typecheck \
	quality \
	test \
	cov \
	check \
	bench \
	build \
	smoke \
	build-check \
	release-check \
	docker \
	docker-check \
	clean

lock:
	poetry lock

install:
	poetry install

format:
	poetry run ruff check --fix .
	poetry run ruff format .

format-check:
	poetry run ruff format --check .

lint:
	poetry run ruff check .

typecheck:
	poetry run mypy src tests benchmarks

quality: format-check lint typecheck

test:
	poetry run pytest

cov:
	poetry run pytest \
		--cov=pcd_cli \
		--cov-branch \
		--cov-report=term-missing \
		--cov-report=html \
		--cov-fail-under=95

check: quality cov

bench:
	poetry run pytest benchmarks -q -s

build:
	rm -rf dist
	poetry build

smoke:
	rm -rf .smoke
	poetry run python -m venv .smoke
	.smoke/bin/python -m pip install dist/*.whl
	.smoke/bin/pcd --version
	.smoke/bin/pcd --help
	rm -rf .smoke

build-check: build smoke

release-check: check build-check

docker:
	docker build --tag pcd-cli:local .

docker-check: docker
	docker run --rm pcd-cli:local --version
	docker run --rm pcd-cli:local --help

clean:
	rm -rf \
		build \
		dist \
		htmlcov \
		.coverage \
		.pytest_cache \
		.mypy_cache \
		.ruff_cache \
		.smoke
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
