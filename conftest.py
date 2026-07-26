"""Make the repo root importable during tests.

A few tests (`test_clinical`, `test_locomo_harness`) pull fixtures and harness code from the
top-level `benchmarks` package, which is dev/test-only and deliberately NOT installed into the
`vayl` wheel. Running `python -m pytest` happens to put the CWD on sys.path, but a bare `pytest`
(what CI runs) does not — so those imports failed only in CI. Adding the root here fixes it under
either invocation, without shipping `benchmarks` in the package.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.resolve()))
