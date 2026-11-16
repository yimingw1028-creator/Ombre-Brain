# ruff: noqa
"""测试替身：让 @pytest.mark.asyncio 的协程测试能跑。"""
import asyncio, inspect
import pytest

fixture = pytest.fixture

def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run coroutine test")

@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    fn = pyfuncitem.obj
    if inspect.iscoroutinefunction(fn):
        args = {a: pyfuncitem.funcargs[a] for a in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(fn(**args))
        return True
