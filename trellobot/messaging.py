"""Module taking care of message sending, editing, etc."""


import logging
from telepot.namedtuple import InlineKeyboardMarkup, InlineKeyboardButton


class Messenger:
    """Send a message and give a chance to edit it."""

    parse_modes = {'md': 'Markdown', 'html': 'HTML'}

    # @staticmethod
    # def from_message(bot, update, msg_handler, parse_mode='md', bufsize=8):
    #    """Build a new Messenger referring to a pre-existing message."""
    #    m = Messenger(bot, update, parse_mode=parse_mode, bufsize=bufsize)
    #    m._msg = msg_handler
    #    return m

    @staticmethod
    def from_ref(bot, chat_id, msg_id, parse_mode='md', bufsize=10):
        """Build a new Messenger referring to a pre-existing message."""
        m = Messenger(bot, {}, parse_mode=parse_mode, bufsize=bufsize)
        m.chat_id = chat_id
        m.msg_id = msg_id
        return m

    # @staticmethod
    # def from_query(bot, query, parse_mode='md', bufsize=8):
    #    """Build a new Messenger tied to a query response."""
    #    return Messenger.from_message(bot, query, query.message,
    #                                  parse_mode, bufsize)

    def __init__(self, bot, update, parse_mode='md', bufsize=10, quiet=False):
        """Create a new context for messaging."""
        logging.debug('Creating a Messenger')
        self.bot = bot
        self.update = update
        self.chat_id = update.get('chat', {}).get('id')
        self._mode = parse_mode
        self._quiet = quiet
        self._bufcap = bufsize  # How many edits to store before sending
        self._bufcount = 0  # How many edits are waiting to be sent
        # If present, send a message immediately, and save a handler
        self._text = ''
        self._keyboard = []

    def mid(self):
        """Return message id this context is (possibly) referring to."""
        if hasattr(self, 'msg_id'):
            return self.msg_id
        return None

    async def spawn(self, message=None, bufsize=10, quiet=False):
        """Spawn a new messenger with same bot, update and parse mode."""
        m = Messenger(self.bot, self.update,
                      self._mode, bufsize, self._quiet)
        await m.send(message)
        return m

    def _make_keyboard(self, keyboard):
        """Build a keyboard markup."""
        if keyboard is not None:
            rows = []
            for row in keyboard:
                keys = []
                for btn in row:
                    # b = InlineKeyboardButton(btn[0], callback_data=btn[1])
                    keys.append(InlineKeyboardButton(**btn))
                rows.append(keys)
            keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
        return keyboard

    async def _edit_text(self, text, keyboard=None):
        """Send current text as editing text, markdown or html."""
        keyboard = self._make_keyboard(keyboard)
        self._msg = await self.bot.editMessageText(
            (self.chat_id, self.msg_id),
            text,
            parse_mode=Messenger.parse_modes.get(self._mode),
            reply_markup=keyboard,
        )
        self.msg_id = self._msg['message_id']

    async def send(self, msg, keyboard=None, quiet=False):
        """Send a text message immediately, with optional keyboard."""
        logging.info(f'Sending message {msg} with mode {self._mode}')
        keyboard = self._make_keyboard(keyboard)
        # Send formatted message with markup
        self._text = msg
        self._msg = await self.bot.sendMessage(
            chat_id=self.chat_id,
            text=msg,
            parse_mode=Messenger.parse_modes.get(self._mode),
            reply_markup=keyboard,
            disable_notification=quiet,
        )
        self.msg_id = self._msg['message_id']

    async def sendPhoto(self, photo, caption=None, quiet=False):
        """Send a photo."""
        self._msg = await self.bot.sendPhoto(
            chat_id=self.chat_id,
            photo=photo,
            caption=caption,
            disable_notification=quiet,
        )
        self.msg_id = self._msg['message_id']

    async def sendSticker(self, sticker, quiet=False):
        """Send a sticker."""
        self._msg = await self.bot.sendSticker(
            chat_id=self.chat_id,
            sticker=sticker,
            disable_notification=quiet,
        )
        self.msg_id = self._msg['message_id']

    async def flush(self):
        """Send content of the buffer immediately."""
        if self._bufcount > 0:
            await self._edit_text(self._text, self._keyboard)
            self._bufcount = 0

    async def append(self, text, markdown=True, keyboard=None):
        """Append text to the message and send it to client."""
        # Append new text
        self._text += text
        if keyboard is not None:
            self._keyboard.extend(keyboard)  # Add rows to keyboard
        self._bufcount += 1  # Increase the number of edits buffered
        # If necessary, flush the buffer
        if self._bufcount >= self._bufcap:
            await self.flush()

    async def override(self, text, markdown=True, keyboard=None):
        """Replace the message with given text."""
        # If message is unchanged, server is unhappy: leave as-is if unchanged
        # logging.info(f'Replacing message "{self._text}" with "{text}"')
        # if self._text == text:
        #    return
        self._text = text
        if keyboard is not None:
            self._keyboard.extend(keyboard)  # Add rows to keyboard
        self._bufcount += 1
        if self._bufcount >= self._bufcap:
            await self.flush()

    async def __aenter__(self):
        """Send the first message and return self."""
        return self

    async def __aexit__(self, exc_ty, exc_va, exc_tb):
        """Do nothing."""
        # Ensure buffer is flushed
        await self.flush()
