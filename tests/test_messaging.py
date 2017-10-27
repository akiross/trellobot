"""Test messaging module."""


from trellobot.messaging import Messenger
from unittest.mock import MagicMock


def test_messenger_spawn():
    """Test that spawn inherits same data."""
    bot = MagicMock()
    update = MagicMock()

    msg = Messenger(bot, update, 'foo', 'bar')
    msg2 = msg.spawn('baz')

    assert msg.bot == msg2.bot
    assert msg.update == msg2.update
    assert msg._mode == msg2._mode
    assert msg._text == 'foo'
    assert msg2._text == 'baz'


def test_messenger_init():
    """Test that a message is sent if specified."""
    bot = MagicMock()
    update = MagicMock()

    # Specify no message
    Messenger(bot, update)
    assert bot.send_message.call_count == 0

    # Specify a message
    Messenger(bot, update, 'foo')
    assert bot.send_message.call_count == 1


def test_messenger_edits():
    """Test that appending works."""
    bot = MagicMock()
    update = MagicMock()

    # Test context manager
    with Messenger(bot, update, 'foo') as msg:
        assert msg._text == 'foo'
        # A message should have been sent
        assert bot.send_message.call_count == 1
        # No edits should have been sent
        assert bot.editMessageText.call_count == 0

        # Edit by append
        msg.append('bar')
        # Flush to send
        # TODO buffer is not tested
        msg.flush()
        assert msg._text == 'foobar'
        # No more send_message
        assert bot.send_message.call_count == 1
        # An edit command should have been issued
        assert bot.editMessageText.call_count == 1

        # Edit by override
        msg.override('qux')
        msg.flush()  # TODO buffering is not tested
        assert msg._text == 'qux'
        # No more send_message
        assert bot.send_message.call_count == 1
        # An edit command should have been issued
        assert bot.editMessageText.call_count == 2
