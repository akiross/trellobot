from trellobot.entities import Board
from trellobot.entities import Card
from trellobot.entities import List
from trellobot.entities import Organization


def test_org():
    o = Organization('oid', 'orgname', False, 'http://trello.com/someorg')
    assert str(o) == '[orgname](http://trello.com/someorg)'


def test_board():
    b = Board('bid', 'boardname', False, 'http://trello.com/someboard')
    assert str(b) == '[boardname](http://trello.com/someboard)'


def test_list():
    l = List('lid', 'listname', 'http://trello.com/somelist')
    assert str(l) == '[listname](http://trello.com/somelist)'


def test_card():
    c = Card('cid', 'cardname', 'http://trello.com/somecard', 'due', False)
    assert str(c) == '\u2610 [cardname](http://trello.com/somecard)'
    c = Card('cid', 'cardname', 'http://trello.com/somecard', 'due', True)
    assert str(c) == '\u2611 [cardname](http://trello.com/somecard)'
