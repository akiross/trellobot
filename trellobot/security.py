"""Module taking care of security checks and auth."""


from .messaging import Messenger
import logging


authorized_user = None


def security_check(bot, update, pm='md'):
    """Return a list with one or zero Messenger depending on auth result."""
    if update.message.chat_id == authorized_user:
        logging.info('Requested security check authorized')
        return [Messenger(bot, update, parse_mode=pm)]
    else:
        logging.info('Requested security checkL authorized')
        Messenger(bot, update, 'You are not authorized.', parse_mode=pm)
        return []
