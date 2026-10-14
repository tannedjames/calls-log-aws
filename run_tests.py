"""
Lightweight stand-in for pytest so tests.py works in environments where pytest
isn't installed. Supports the small subset of pytest used in tests.py
(@pytest.mark.parametrize and the fail() / raises() helpers — though we don't
use raises here).

If real pytest is installed, prefer it:  python -m pytest tests.py -v
This shim is just to run the same tests anywhere.

Usage:
    python run_tests.py
"""

from __future__ import annotations

import inspect
import sys
import traceback
import types


class _Mark:
    def __init__(self):
        self.parametrize_args = None

    def parametrize(self, argnames, argvalues):
        def decorator(fn):
            fn._parametrize = (argnames, list(argvalues))
            return fn
        return decorator


class _PytestShim(types.ModuleType):
    def __init__(self):
        super().__init__("pytest")
        self.mark = _Mark()

    @staticmethod
    def fail(msg):
        raise AssertionError(msg)


def _install_shim():
    if "pytest" not in sys.modules:
        sys.modules["pytest"] = _PytestShim()


def _expand_params(fn):
    """Yield (display_name, callable_with_no_args) for a test function."""
    if hasattr(fn, "_parametrize"):
        argnames, argvalues = fn._parametrize
        names = [n.strip() for n in argnames.split(",")]
        for i, vals in enumerate(argvalues):
            if not isinstance(vals, (tuple, list)):
                vals = (vals,)
            kwargs = dict(zip(names, vals))
            display = f"{fn.__name__}[{i}: {kwargs}]"
            yield display, (lambda kw=kwargs: fn(**kw))
    else:
        yield fn.__name__, fn


def main():
    _install_shim()
    import tests  # noqa: E402

    passed = 0
    failed = 0
    failures = []

    members = [
        (name, obj) for name, obj in inspect.getmembers(tests)
        if name.startswith("test_") and callable(obj)
    ]

    for name, fn in members:
        for display, runner in _expand_params(fn):
            try:
                runner()
                passed += 1
                print(f"  PASS  {display}")
            except Exception:
                failed += 1
                failures.append((display, traceback.format_exc()))
                print(f"  FAIL  {display}")

    print()
    print(f"Results: {passed} passed, {failed} failed")
    if failures:
        print("\n--- Failure detail ---")
        for display, tb in failures:
            print(f"\n* {display}")
            print(tb)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
