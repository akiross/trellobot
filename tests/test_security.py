import trellobot.security
from trellobot.security import security_check
from trellobot.messaging import Messenger
from unittest.mock import MagicMock, patch
import pytest
import asyncio


@pytest.mark.asyncio
async def test_security_check(amocker):
    """Test security check."""
    # Mock the messenger: we don't have to send anything right now
    with patch('trellobot.security.Messenger') as mockmsg:
        # Set what user is currently authorized
        trellobot.security.authorized_user = 1234
        # Mock an instance of Messenger
        m = mockmsg()
        # The send function is async def and must be mocked
        m.send = amocker()
        # Prepare an update from the user
        update = {'chat': {'id': 1234}}
        # Test security check passing
        for ctx in await security_check(None, update):
            assert ctx == m
        # We are still not sure security_check returned a non-empty iterable!
        # Messenger was called once, inside security_check, so check that
        assert mockmsg.call_count == 2
        # No message was sent
        assert m.send.call_count == 0

        # Test another user
        update = {'chat': {'id': 4321}}
        for ctx in await security_check(None, update):
            assert False # This should never happen
        # Another Messenger was created
        assert mockmsg.call_count == 3
        # A message was sent
        assert m.send.call_count == 1
