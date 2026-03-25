# Project Agent Operations

## Dedicated Conda Environment

Use a dedicated conda environment for this repository to avoid package/version drift.

1. Create environment (first time):

```bash
conda env create -f environment.yml
```

2. Activate environment:

```bash
conda activate legonanobot
```

3. Verify runtime:

```bash
python --version
which python
```

## Test Execution Rule

Always run tests with local package precedence:

```bash
PYTHONPATH=. pytest -q
```

Reason: plain pytest may import an installed nanobot package from another environment, causing misleading failures.

## Daily Workflow

1. Pull latest code.
2. Activate conda env: `conda activate legonanobot`.
3. Sync dependencies when needed: `pip install -e .[dev]`.
4. Run focused tests with `PYTHONPATH=. pytest -q <test-files>`.
