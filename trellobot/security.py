"""Module taking care of security checks and auth."""


from .messaging import Messenger
import logging


authorized_user = None


async def security_check(bot, update, pm='md'):
    """Return a list with one or zero Messenger depending on auth result."""
    logging.info(f'Asked security check {bot} {update}')
    m = Messenger(bot, update, parse_mode=pm)
    if update['chat']['id'] == authorized_user:
        logging.info('Requested security check authorized')
        return [m]
    else:
        logging.info('Requested security check authorized')
        await m.send('You are not authorized.')
        return []
