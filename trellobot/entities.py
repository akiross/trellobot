"""Module defining Trello entities used in the bot."""


from collections import namedtuple


class Organization(namedtuple('Organization', 'id name blacklisted url')):
    """A Trello organization."""

    def __str__(self):
        """Organization to string, markdown formatted."""
        return f'[{self.name}]({self.url})'


class Board(namedtuple('Board', 'id name blacklisted url')):
    """A Trello board."""

    def __str__(self):
        """Board to string, markdown formatted."""
        return f'[{self.name}]({self.url})'


class List(namedtuple('List', 'id name idBoard subscribed')):
    """A Trello list."""

    def __str__(self):
        """List to string, markdown formatted."""
        return f'{self.name}'


class Card(namedtuple('Card', 'id name url due dueComplete')):
    """A Trello card."""

    def __str__(self):
        """Card to string, markdown formatted."""
        if self.dueComplete:
            return f'\u2611 [{self.name}]({self.url})'
        else:
            return f'\u2610 [{self.name}]({self.url})'
