"""Common stuff for tests, useless right now."""

import pytest
from asyncio import coroutine
from unittest.mock import MagicMock


# Some data as dictionary
dict_data = {}

# Some data as json
json_data = ''


@pytest.fixture
def amocker():
    """Use this to mock an async def function."""
    def _mocker():
        coro = MagicMock(name='CoroutineResult')
        corofunc = MagicMock(name='CoroutineFunction', side_effect=coroutine(coro))
        corofunc.coro = coro
        return corofunc
    return _mocker


@pytest.fixture
def bot(amocker):
    """Return a bot with async method mocked."""
    bot = MagicMock()
    bot.sendMessage = amocker()
    bot.editMessageText = amocker()
    return bot
