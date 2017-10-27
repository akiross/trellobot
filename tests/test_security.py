from trellobot.security import security_check
from trellobot.messaging import Messenger
from unittest.mock import MagicMock, patch


def test_security_check():
    """Test security stuff."""
    bot = MagicMock()
    update = MagicMock()

    # Set my chat id
    update.message.chat_id = int(open('allowed.txt').read().strip())
    # Check that right chat id will pass
    for ctx in security_check(bot, update):
        # We should enter the block
        assert isinstance(ctx, Messenger)

    # Change to unauth user
    update.message.chat_id = 123456
    with patch('trellobot.security.Messenger') as mockmsg:
        # Check that other chat id will not pass
        for ctx in security_check(bot, update):
            # We should NOT enter in this block
            assert False
        # Check that message was sent
        assert mockmsg.call_count == 1
