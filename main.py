#!/usr/bin/env python3
"""Starts TrelloBot."""


# Some logging
import logging

import trellobot.security as sec
from trellobot.bot import TrelloBot
import os


if __name__ == '__main__':
    # Some logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Read authorized user ID
    sec.authorized_user = int(open('allowed.txt').read().strip())

    # Key for using telegram bot
    bot_key = open('bot.txt').read().strip()

    # Keys for accessing trello
    trello_key = open('key.txt', 'rt').read().strip()
    trello_secret = open('secret.txt', 'rt').read().strip()
    trello_token = open('token.txt', 'rt').read().strip()

    # Create bot and run polling main loop
    tb = TrelloBot(trello_key, trello_secret, trello_token)
    tb.run_async(bot_key, os.environ.get('TRELLOBOT_URL', '127.0.0.1:8080'))
