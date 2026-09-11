"""
Configurações Globais e Hooks do Pytest para Vertex-bot.
Permite executar testes com corrotinas assíncronas nativamente.
"""

import asyncio
import inspect
from typing import Any


def pytest_pyfunc_call(pyfuncitem: Any) -> bool | None:
    """Executa testes assíncronos (async def) usando o event loop nativo do asyncio."""
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        argnames = pyfuncitem._fixtureinfo.argnames
        kwargs = {arg: pyfuncitem.funcargs[arg] for arg in argnames if arg in pyfuncitem.funcargs}
        asyncio.run(pyfuncitem.obj(**kwargs))
        return True
    return None
