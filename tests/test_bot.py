"""Test for the actual bot."""


import pytest
import asyncio
from datetime import timedelta
from unittest.mock import MagicMock
from unittest.mock import patch
from trellobot.bot import JobQueue
from trellobot.bot import aware_now
from trellobot.bot import TrelloBot


def in_a_bit(delay_sec):
    return aware_now() + timedelta(seconds=delay_sec)

@pytest.mark.asyncio
async def test_job_queue():
    """Test that job queue works as expected."""
    callback = MagicMock()
    jq = JobQueue()
    # Wait one second
    jq.run_once(callback, 0.1, 'foo', bar='baz')
    await asyncio.sleep(0.5)
    callback.assert_called_once_with('foo', bar='baz')


@pytest.mark.asyncio
async def test_bot_init():
    """Test that bot initializes Trello client when started."""
    with patch('trellobot.bot.TrelloManager') as tm:
        bot = TrelloBot('k', 's', 't')
        tm.assert_called_once_with(api_key='k', api_secret='s', token='t')
        

@pytest.mark.asyncio
async def test_bot_card_notification(amocker):
    """Test that bot correctly setup a card notification."""
    #card = MagicMock()
    # Card is due in a second
    #card.due = aware_now() + 1
    with patch('trellobot.bot.TrelloManager') as tm:
        bot = TrelloBot('k', 's', 't')
        # Card to notify due in half a second
        card = MagicMock()
        card.due = in_a_bit(0.5)
        # Context for notification
        ctx = MagicMock()
        ctx.send = amocker()
        # Register notification
        bot._card_notification(ctx, card)
        # Wait some time
        await asyncio.sleep(1)
        # Check correctness
        ctx.send.assert_called_once()


@pytest.mark.asyncio
async def test_bot_schedule_due(amocker):
    """Test that scheduling a job for a due card works."""
    bot = TrelloBot('k', 's', 't')
    ctx = MagicMock()
    ctx.send = amocker()
    job_queue = MagicMock()

    # Cards in the past are not scheduled, but a notification will be sent if
    # they are not completed
    past_card = MagicMock()
    past_card.due = in_a_bit(-1)
    past_card.dueComplete = False

    # A notification should be added, but card shall not be scheduled
    assert not await bot._schedule_due(ctx, past_card, job_queue)
    assert past_card in bot._pending_notifications
    assert job_queue.run_once.call_count == 0

    # If the card is complete, do not even notify
    bot._pending_notifications.clear()
    past_card.dueComplete = True
    assert not await bot._schedule_due(ctx, past_card, job_queue)
    assert past_card not in bot._pending_notifications
    assert job_queue.run_once.call_count == 0

    # If the card is too far in the past, no notification shall be added
    bot._pending_notifications.clear()
    past_card.due = in_a_bit(-5000 * TrelloBot.past_due_notif_limit)
    past_card.dueComplete = False
    assert not await bot._schedule_due(ctx, past_card, job_queue)
    assert past_card not in bot._pending_notifications
    assert job_queue.run_once.call_count == 0

    # If the card is due soon, a message shall be sent immediately
    # but not scheduled
    now_card = MagicMock()
    now_card.due = in_a_bit(1)
    now_card.dueComplete = False
    assert not await bot._schedule_due(ctx, now_card, job_queue)
    assert now_card not in bot._pending_notifications
    assert job_queue.run_once.call_count == 0
    ctx.send.assert_called_once()

    # If the card is due in a long time, a notification must be queued
    future_card = MagicMock()
    future_card.due = in_a_bit(5000 * TrelloBot.past_due_notif_limit)
    future_card.dueComplete = False
    assert await bot._schedule_due(ctx, future_card, job_queue)
    job_queue.run_once.assert_called_once()
