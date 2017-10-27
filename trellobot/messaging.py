"""Module taking care of message sending, editing, etc."""


import logging
from telegram import ParseMode, InlineKeyboardButton, InlineKeyboardMarkup


class Messenger:
    """Send a message and give a chance to edit it."""

    parse_modes = {'md': ParseMode.MARKDOWN, 'html': ParseMode.HTML}

    @staticmethod
    def from_message(bot, update, msg_handler, parse_mode='md', bufsize=0):
        """Build a new Messenger referring to a pre-existing message."""
        m = Messenger(bot, update, parse_mode=parse_mode, bufsize=bufsize)
        m._msg = msg_handler
        return m

    @staticmethod
    def from_query(bot, query, parse_mode='md', bufsize=0):
        """Build a new Messenger tied to a query response."""
        return Messenger.from_message(bot, query, query.message,
                                      parse_mode, bufsize)

    def __init__(self, bot, update, message=None, parse_mode='md', bufsize=8):
        """Create a new context for messaging."""
        logging.debug('Creating a Messenger')
        self.bot = bot
        self.update = update
        self._mode = parse_mode
        self._bufcap = bufsize  # How many edits to store before sending
        self._bufcount = 0  # How many edits are waiting to be sent
        # If present, send a message immediately, and save a handler
        self._text = ''
        self._keyboard = []
        if message is not None:
            self._text = message
            self._msg = self.send(message)

    def spawn(self, message=None, bufsize=8):
        """Spawn a new messenger with same bot, update and parse mode."""
        return Messenger(self.bot, self.update, message, self._mode)

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
            keyboard = InlineKeyboardMarkup(rows)
        return keyboard

    def _edit_text(self, text, keyboard=None):
        """Send current text as editing text, markdown or html."""
        keyboard = self._make_keyboard(keyboard)
        self._msg = self.bot.editMessageText(
            text=text,
            chat_id=self.update.message.chat_id,
            message_id=self._msg.message_id,
            parse_mode=Messenger.parse_modes.get(self._mode),
            reply_markup=keyboard,
        )

    def send(self, msg, keyboard=None):
        """Send a text message immediately, with optional keyboard."""
        logging.info(f'Sending message {msg} with mode {self._mode}')
        keyboard = self._make_keyboard(keyboard)
        # Send formatted message with markup
        return self.bot.send_message(
            chat_id=self.update.message.chat_id,
            text=msg,
            parse_mode=Messenger.parse_modes.get(self._mode),
            reply_markup=keyboard,
        )

    def flush(self):
        """Send content of the buffer immediately."""
        if self._bufcount > 0:
            self._edit_text(self._text, self._keyboard)
            self._bufcount = 0

    def append(self, text, markdown=True, keyboard=None):
        """Append text to the message and send it to client."""
        # Append new text
        self._text += text
        if keyboard is not None:
            self._keyboard.extend(keyboard)  # Add rows to keyboard
        self._bufcount += 1  # Increase the number of edits buffered
        # If necessary, flush the buffer
        if self._bufcount >= self._bufcap:
            self.flush()

    def override(self, text, markdown=True, keyboard=None):
        """Replace the message with given text."""
        self._text = text
        if keyboard is not None:
            self._keyboard.extend(keyboard)  # Add rows to keyboard
        self._bufcount += 1
        if self._bufcount >= self._bufcap:
            self.flush()

    def __enter__(self):
        """Send the first message and return self."""
        return self

    def __exit__(self, exc_ty, exc_va, exc_tb):
        """Do nothing."""
        # Ensure buffer is flushed
        self.flush()
