"""Test interface to Trello."""


from trellobot.trello import TrelloManager
from trellobot.entities import Organization
from unittest.mock import patch
from types import MethodType


def test_org_names():
    """Test that organization names are correctly collected."""
    with patch('trellobot.trello.TrelloClient'):  # as tcmock:
        tm = TrelloManager(1, 2, 3)
        # Monkey-patch fetch_orgs to return some orgs
        names = {'foo', 'bar', 'baz'}

        def fake_fetch_orgs(self):
            return [Organization('', n, False, '') for n in names]
        tm.fetch_orgs = MethodType(fake_fetch_orgs, tm)
        assert tm.org_names() == names


def test_fetch_org():
    """Test that a JSon is correctly converted into Organizations."""
    with patch('trellobot.trello.TrelloClient') as tcmock:
        tc = tcmock()  # Instance trello client
        tm = TrelloManager(1, 2, 3)
        assert tm._cl == tc  # tc must be the instance used in tm
        orgs = [
            {'id': 0, 'name': 'foo', 'url': 'http://foo'},
            {'id': 1, 'name': 'bar', 'url': 'http://bar'},
            {'id': 2, 'name': 'baz', 'url': 'http://baz'},
            {'id': 3, 'name': 'qux', 'url': 'http://qux'},
        ]
        tc.fetch_json.return_value = orgs  # Values to return
        tm._wl_org = [1, 3]  # Only odd IDs are not blacklisted
        for oo, of in zip(orgs, tm.fetch_orgs()):
            assert oo['id'] == of.id
            assert oo['name'] == of.name
            assert oo['url'] == of.url
            assert of.blacklisted == (oo['id'] % 2 == 0)


def test_fetch_all_boards():
    """Test all boards are correctly read from json."""
    with patch('trellobot.trello.TrelloClient') as tcmock:
        tc = tcmock()
        tm = TrelloManager(1, 2, 3)
        assert tm._cl == tc
        boards = [
            {'id': 0, 'name': 'foo', 'url': 'http://foo', 'idOrganization': 0},
            {'id': 1, 'name': 'bar', 'url': 'http://bar', 'idOrganization': 0},
            {'id': 2, 'name': 'baz', 'url': 'http://baz', 'idOrganization': 1},
            {'id': 3, 'name': 'qux', 'url': 'http://qux', 'idOrganization': 2},
            {'id': 4, 'name': 'nom', 'url': 'http://nom', 'idOrganization': 3},
        ]
        tc.fetch_json.return_value = boards
        # Orgs are currently not involved in board blacklisting
        # tm._wl_org = [1, 3]  
        tm._wl_brd = [1, 3]
        # Prepare some fake orgs
        for bo, bf in zip(boards, tm.fetch_boards(None)):
            assert bo['id'] == bf.id
            assert bo['name'] == bf.name
            assert bo['url'] == bf.url
            assert bf.blacklisted == (bf.id not in tm._wl_brd)
            # Orgs are currently not involved in board blacklisting
            # assert bf.blacklisted == (bo['idOrganization'] not in tm._wl_org)


def test_fetch_org_boards():
    """Test boards from an org are correctly read from json."""
    with patch('trellobot.trello.TrelloClient') as tcmock:
        tc = tcmock()
        tm = TrelloManager(1, 2, 3)
        assert tm._cl == tc
