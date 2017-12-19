"""Manage all the trello things."""


from trello import TrelloClient
from dateutil.parser import parse as parse_date
from trellobot.entities import Organization, Board, List, Card
import logging


class TrelloManager:
    """Manage Trello connection and data."""

    def __init__(self, api_key, api_secret, token):
        """Create a new TrelloManager using provided keys."""
        self._cl = TrelloClient(
            api_key=api_key,
            api_secret=api_secret,
            token=token,
        )

        # Start whitelisting no organization
        self._wl_org = set()
        # Start whitelisting no board
        self._wl_brd = set()

    def whitelist_org(self, oid):
        """Add an organization to whitelist, by ID."""
        self._wl_org.add(oid)

    def blacklist_org(self, oid):
        """Remove an organization from whitelist, by ID."""
        self._wl_org.discard(oid)

    def whitelist_brd(self, bid):
        """Whitelist a board by id."""
        self._wl_brd.add(bid)

    def blacklist_brd(self, bid):
        """Blacklist a board by id."""
        self._wl_brd.discard(bid)

    def org_names(self):
        """Fetch and return organization names."""
        return {o.name for o in self.fetch_orgs()}

    def fetch_orgs(self):
        """Generate organizations and their blacklistedness."""
        for o in self._cl.fetch_json('/members/me/organizations/'):
            yield Organization(o['id'], o['name'],
                               o['id'] not in self._wl_org, o['url'])

    def fetch_boards(self, org=None):
        """Generate boards (in given org) and their blacklistedness."""
        if org is None:
            for b in self._cl.fetch_json('/members/me/boards/'):
                # If board has not an organization, it is blacklisted iff
                # it's not in the whitelist
                bbl = b['id'] not in self._wl_brd

                # If board has an organization, it is blacklisted iff
                # both board or organization are not in whitelist
                # if b['idOrganization'] is not None:
                #    bbl = bbl or b['idOrganization'] not in self._wl_org

                yield Board(b['id'], b['name'], bbl, b['url'])
        else:
            orgs = list(self.fetch_orgs())
            id2na = {o.id: o.name for o in orgs}
            na2id = {o.name: o.id for o in orgs}

            # Convert names to id
            if org in na2id:
                org = na2id[org]

            # Cannot find ID
            if org not in id2na:
                return

            for b in self._cl.fetch_json(f'/organizations/{org}/boards/'):
                bl = b['id'] not in self._wl_brd
                yield Board(b['id'], b['name'], bl, b['url'])

    def fetch_lists(self, bid=None):
        """Generate lists in given board."""
        # Use all boards if none was specified
        bids = list(b.id for b in self.fetch_boards()) if bid is None else [bid]
        # Yield all the lists
        for b in bids:
            for l in self._cl.fetch_json(f'/boards/{b}/lists'):
                logging.info('Got list ' + l['id'])
                yield List(l['id'], l['name'], l['idBoard'], l['subscribed'])

    def get_card(self, cid):
        """Get a card by ID."""
        c = self._cl.fetch_json(f'/cards/{cid}')
        if c['due'] is not None:
            c['due'] = parse_date(c['due'])
        return Card(c['id'], c['name'], c['url'], c['due'], c['dueComplete'])

    def fetch_cards(self, lid=None, bid=None):
        """Generate cards from list, board or everything."""
        if bid is not None:
            seq = self._cl.fetch_json(f'/boards/{bid}/cards')
        elif lid is not None:
            seq = self._cl.fetch_json(f'/lists/{lid}/cards')
        else:
            seq = self._cl.fetch_json(f'/members/me/cards')

        for c in seq:
            if c['due'] is not None:
                c['due'] = parse_date(c['due'])
            yield Card(c['id'], c['name'],
                       c['url'], c['due'], c['dueComplete'])

    def create_card(self, lid, name):
        """Create a card inside the specified list."""
        c = self._cl.fetch_json(f'/cards', http_method='POST', post_args={
            'idList': lid,
            'name': name,
        })
        return Card(c['id'], c['name'], c['url'], c['due'], c['dueComplete'])
