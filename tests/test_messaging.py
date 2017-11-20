"""Test messaging module."""


import pytest
from trellobot.messaging import Messenger
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_messenger_spawn(bot):
    """Test that spawn inherits same data."""
    update = MagicMock()

    msg = Messenger(bot, update)
    msg2 = await msg.spawn('foo')

    assert msg.bot == msg2.bot
    assert msg.update == msg2.update
    assert msg._mode == msg2._mode
    assert msg._text == ''
    assert msg2._text == 'foo'


@pytest.mark.asyncio
async def test_messenger_edits(bot):
    """Test that appending works."""
    update = MagicMock()

    # Test context manager
    async with Messenger(bot, update) as msg:
        # No message should have been sent
        assert bot.sendMessage.call_count == 0
        # No edits should have been sent
        assert bot.editMessageText.call_count == 0
        # No message is sent
        assert not hasattr(msg, 'msg_id')

        # Send a message: sent but not edited
        await msg.send('foo')
        assert bot.sendMessage.call_count == 1
        assert bot.editMessageText.call_count == 0
        assert hasattr(msg, 'msg_id')

        await msg.append('bar')
        # Flush to send
        # TODO buffer is not tested
        await msg.flush()
        assert msg._text == 'foobar'
        # No more send_message
        assert bot.sendMessage.call_count == 1
        # An edit command should have been issued
        assert bot.editMessageText.call_count == 1

        # Edit by override
        await msg.override('qux')
        await msg.flush()  # TODO buffering is not tested
        assert msg._text == 'qux'
        # No more send_message
        assert bot.sendMessage.call_count == 1
        # An edit command should have been issued
        assert bot.editMessageText.call_count == 2
