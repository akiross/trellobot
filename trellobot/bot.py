"""Implementation of the actual bot."""

# Web served interface
import asyncio
from aiohttp import web

# Some logging
import logging
import secrets

import trellobot.security as security
from trellobot.messaging import Messenger
from trellobot.trello import TrelloManager

import humanize
from datetime import datetime
from datetime import timezone

from collections import Counter

from functools import partial
import traceback


def aware_now():
    """Return TZ-aware now."""
    return datetime.now(timezone.utc)


def tznaive(d):
    """Return where a datetime object is TZ naive or not."""
    return d.tzinfo is None or d.tzinfo.utcoffset(d) is None


class JobQueue:
    """Manages async jobs."""

    def run_once(self, callback, delay, *args, **kwargs):
        """Schedule a callback to be executed after given delay (seconds)."""
        loop = asyncio.get_event_loop()
        return loop.call_later(delay, partial(callback, *args, **kwargs))


class TrelloBot:
    """Bot to make Trello perfect."""

    update_int = 10  # Update interval in minutes
    notify_int = 2  # Notification interval in hours
    past_due_notif_limit = 24  # Time limit for past due to consider, in hours
    due_soon_notif_limit = 1  # Time limit for due soon to consider time limit, in hours
    todo_as_default = True  # When a message is not understood, add it to todos

    def __init__(self, trello_key, trello_secret, trello_token):
        """Initialize a TrelloBot, reading key files."""
        # Time of last check
        self.last_check = aware_now()
        # Due dates for registered cards
        self._dues = {}
        # Notification jobs
        self._jobs = {}
        # Job for repeating updates
        self._job_check = None
        # Job for repeating notifications
        self._job_notif = None
        self._pending_notifications = set()
        # Try to be as quiet as possible (remember: user can mute it)
        self._quiet = True
        # What lists are we inserting TODOs?
        self._todo_list = None

        self._trello = TrelloManager(
            api_key=trello_key,
            api_secret=trello_secret,
            token=trello_token,
        )

    def _card_notification(self, ctx, card):
        """Notify that a card is due shortly."""
        async def _notify(when):
            await ctx.send(f'Card {card} due {humanize.naturaltime(when)}')
        loop = asyncio.get_event_loop()
        loop.create_task(_notify(aware_now() - card.due))

    async def _schedule_due(self, ctx, card, job_queue):
        """Schedule a job for due card, return True if actually enqueued."""
        # We are using time-aware dates, telegram API isn't:
        # convert to delay instead of using directly a datetime
        # If card is complete, nothing happens
        if card.dueComplete:
            return False
        delay = (card.due - aware_now()).total_seconds()
        # If due date is past, we might handle it anyway
        if delay < 0:
            # Notify: you had a non-completed card in the last 24 hours!
            if delay > -3600 * TrelloBot.past_due_notif_limit:
                logging.info(f'Non-sched card with recently past due {card}')
                self._dues[card.id] = card.due  # TODO devo salvare questa card?
                self._pending_notifications.add(card)
            else:
                logging.info(f'Non-sched card with far past due {card}')
            return False
        else:
            # In case of positive delay, we want to notify some time *before*
            # the actual due date
            delay -= 3600 * TrelloBot.due_soon_notif_limit
            # If there is no time, notify immediately!
            if delay < 0:
                logging.info(f'Non-scheduling card due soon {card}')
                await ctx.send(f'Card {card} is due soon!')
                # Add the card to upcoming cards
                self._dues[card.id] = card.due
                return False
            else:
                logging.info(f'Scheduling card due in future {card}')

        # Schedule a notification and save the job for this card
        self._jobs[card.id] = job_queue.run_once(
            self._card_notification,
            delay,
            ctx, card)
        self._dues[card.id] = card.due  # Save original due date
        return True

    async def _reschedule_due(self, card, ctx, job_queue):
        """Reschedule a job for due card."""
        self._unschedule_due(card.id, ctx, job_queue)
        await self._schedule_due(ctx, card, job_queue)

    def _unschedule_due(self, cid, ctx, job_queue):
        """Unschedule a job previously set for due card."""
        self._jobs[cid].cancel()
        del self._jobs[cid]
        del self._dues[cid]  # Removed associated due date

    async def _update_due(self, bid, ctx, jq):
        """Update due dates for given board."""
        # Iterate cards in board and add to due dates
        count = Counter()
        scanned = set()  # IDs of scanned cards
        for c in self._trello.fetch_cards(bid=bid):
            scanned.add(c.id)
            # Card has no due date set
            if c.due is None:
                if c.id not in self._jobs:
                    # Due was not recorded previously: we can safely skip
                    count['ignored'] += 1
                    continue
                else:
                    # Due was recorded, but removed: remove the card
                    self._unschedule_due(c.id, ctx, jq)
                    # Count removed card
                    count['unscheduled'] += 1
            else:
                logging.info(f'Card {c.name} has due date.')
                # Card has due date set
                if c.id not in self._jobs:
                    logging.info(f'Card {c.name} is not scheduled yet')
                    # Card is not scheduled: it could be new or completed
                    if c.dueComplete:
                        logging.info(f'Card {c.name} is complete')
                        # If card is complete, ignore it
                        count['ignored'] += 1
                    elif await self._schedule_due(ctx, c, jq):
                        # Card were actually accepted for scheduling, likely
                        # because due date is in the future
                        count['scheduled'] += 1
                    else:
                        logging.info(f'Card {c.name} was ignored for scheduling')
                        # Card was not scheduled, maybe for due date in past
                        # or because notification was sent immediately
                        count['ignored'] += 1
                else:
                    logging.info(f'Card {c.name} is already scheduled.')
                    # Card has due date and it is scheduled
                    if self._dues[c.id] == c.due:
                        if c.dueComplete:
                            # Card was completed, unschedule notification
                            count['completed'] += 1
                            self._unschedule_due(c.id, ctx, jq)
                        else:
                            # Card is still incomplete, leave the job as is
                            count['unchanged'] += 1
                    else:
                        # Card already present, but due date was changed
                        self._reschedule_due(c, ctx, jq)  # Reschedule the job
                        count['rescheduled'] += 1
        logging.info(f'update_due stats: {count} {scanned}')
        return count, scanned

    async def _check_due(self, ctx, job_queue):
        """Rebuild the dictionary of due dates."""
        # Iterate all the boards
        count = Counter()
        scanned = set()
        for b in self._trello.fetch_boards():
            if not b.blacklisted:
                logging.info(f'checking due dates for board {b}')
                c, s = await self._update_due(b.id, ctx, job_queue)
                count += c
                scanned.update(s)
        # Check for removed cards (TODO get notifications from trello?)
        saved = set(self._dues.keys())
        for cid in saved - scanned:
            self._unschedule_due(cid, ctx, job_queue)
            count['deleted'] += 1
        # Return counter
        return count

    async def _update(self, ctx, job_queue, quiet):
        """Rescan cards tracking due dates."""
        if quiet:
            await self._check_due(ctx, job_queue)
        else:
            stm = '*Status*: Scanning for updates...'
            async with await ctx.spawn(stm) as msg:
                # Get data, caching them
                count = await self._check_due(ctx, job_queue)
                # n = len(list(self._trello.fetch_data()))
                await msg.override(f'*Status*: Done. ' + self._report(count))

    async def check_updates(self, ctx, job_queue):
        """Check if new threads are present since last check."""
        logging.info('JOB: checking updates')
        await self._update(ctx, job_queue, self._quiet)

    async def check_notifications(self, ctx, job_queue):
        """Check if there are pending notifications."""
        logging.info('JOB: checking pending notifications')
        for card in self._pending_notifications:
            logging.info(f'Processing pending card {card.name}')
            # Check if card was completed while we waited to notify
            if card.dueComplete:
                continue  # Alright, done
            # Else, check if it was past due or not
            delay = (card.due - aware_now()).total_seconds()
            # FIXME messages should specify when the card was due
            # in a human-friendly format (e.g is due in 3 hours, was due 12 hours ago)
            if delay < 0 and delay > -3600 * TrelloBot.past_due_notif_limit:
                await ctx.send(f'Card was due in the last {TrelloBot.past_due_notif_limit} hour(s)! {card}')
        # We notified everything
        self._pending_notifications.clear()

    def _report(self, count):
        """Produce a report regarding count."""
        return ', '.join([f'{v} cards {k}' for k, v in count.items()])

    async def rescan_updates(self, ctx, job_queue):
        """Rescan cards tracking due dates."""
        # User requested an explicit update, so disable quiet mode
        await self._update(ctx, job_queue, False)

    async def lso(self, ctx):
        """List organizations."""
        logging.info('Requested /lso')
        async with await ctx.spawn('*Organizations:*\n') as msg:
            later = []
            for o in self._trello.fetch_orgs():
                if o.blacklisted:
                    later.append(f' - {o} {o.id}')
                else:
                    await msg.append(f' - {o} {o.id}\n')
            # Print blacklisted organizations after
            if later:
                await msg.append('Blacklisted:\n')
                await msg.append('\n'.join(later))

    async def lsb(self, ctx):
        """List boards, optionally filtering by organization."""
        logging.info('Requested /lsb')
        args = self._get_args(ctx)
        if len(args) > 1:
            org = args[1]
        else:
            org = None
        def lsl_url(b):
            return f'[lsl]({self.url}/api/{self.sec_tok}/lsl/{b.id})'
        async with await ctx.spawn(f'*Boards in* {org}:\n') as msg:
            later = []
            for b in self._trello.fetch_boards(org):
                if b.blacklisted:
                    later.append(f' - {b} {lsl_url(b)}')
                else:
                    await msg.append(f' - {b} {lsl_url(b)}\n')
            if later:
                await msg.append('Blacklisted:\n')
                await msg.append('\n'.join(later))

    async def _lsl(self, bid):
        def set_default(l):
            return f'[TODO]({self.url}/api/{self.sec_tok}/set/todo/{l.id})'

        fake_update = {'chat': {'id': security.authorized_user}}
        ctx = Messenger(self.bot, fake_update)
        async with await ctx.spawn(f'*Lists in* {bid}:\n') as msg:
            for l in self._trello.fetch_lists(bid):
                await msg.append(f' - {l} {l.id} {set_default(l)}\n')

    async def lsl(self, ctx):
        """List lists in the specified board."""
        logging.info('Requested /lsl')
        args = self._get_args(ctx)
        bid = None
        if len(args) == 2:
            # Search for a matching board ID
            for b in self._trello.fetch_boards():
                if args[1] == b.id:
                    bid = b.id
                    break
        elif len(args) == 3:
            # Search for a board matching by ID or name
            org = args[1]
            brd = args[2]
            for b in self._trello.fetch_boards(org):
                if brd == b.id or brd == b.name:
                    bid = b.id
                    break
        # We need a board ID, error and quit if missing
        if bid is None:
            await ctx.send('I need at least a board ID, or an org and board')
            return
        await self._lsl(bid)

    async def web_lsl(self, request):
        if request.match_info.get('token') != self.sec_tok:
            return web.Response(text='Not authorized', status=401)
        bid = request.match_info.get('bid')
        await self._lsl(bid)
        return web.Response(text='Listing board contents.')

    async def preferences(self, ctx, job_queue):
        """Process user preferences."""
        tokens = self._get_args(ctx)
        try:
            if tokens[1] == 'update':
                if tokens[2] == 'interval':
                    t = float(tokens[3])  # Index and value can fail
                    # Bound the maximum check interval in [30 seconds, 1 day]
                    TrelloBot.update_int = min(max(t, 0.3), 60 * 24)
                    await ctx.send(f'Interval set to {TrelloBot.update_int} minutes')
                    self._schedule_repeating_updates(ctx, job_queue)
            elif tokens[1] == 'notification':
                if tokens[2] == 'interval':
                    if tokens[3] == 'off':
                        self._cancel_repeating_notifications()
                        await ctx.send('Repeating notifications off')
                    elif tokens[3] == 'on':
                        self._schedule_repeating_notifications(ctx, job_queue)
                        await ctx.send('Repeating notifications on')
                    else:
                        t = float(tokens[3])  # Index and value can fail
                        # Bound the maximum check interval in [30 seconds, 1 day]
                        TrelloBot.notify_int = min(max(t, 0.1), 24)
                        await ctx.send(f'Interval set to {TrelloBot.notify_int} hours')
                        self._schedule_repeating_notifications(ctx, job_queue)
            elif tokens[1] == 'quiet':
                self._quiet = True
                await ctx.send('I will be quieter now')
            elif tokens[1] == 'verbose':
                self._quiet = False
                await ctx.send('I will talk a bit more now')
            elif tokens[1] == 'todo':
                async with await ctx.spawn(f'Checking...') as msg:
                    if self._set_todo(tokens[2].strip()):
                        await msg.override('Default list has ben set')
                    else:
                        await msg.override('No such list')
        except Exception as exc:
            traceback.print_tb(exc.__traceback__)
            # Whatever goes wrong
            await ctx.send(
                f'*Settings help*\n'
                f'/set update interval [0.3:{60*24}] _update interval (mins)_ **Now:** {TrelloBot.update_int}\n'
                f'/set notification interval  [on|off|0.1:24] _notification interval (hours)_ **Now:** {TrelloBot.notify_int}\n'
                f'/set quiet _make bot quieter_ **Now:** {self._quiet}\n'
                f'/set verbose _make bot verbose_ **Now:** {not self._quiet}\n'
                f'/set todo [list ID] **Now:** {self._todo_list}\n'
            )

    def _set_todo(self, lid):
        """Verify if the list ID is valid and can be used to set TODO."""
        # res = lid in (l.id for l in self._trello.fetch_lists())
        res = True  # Let's trust the system...
        if res:
            self._todo_list = lid
        return res

    async def web_set_todo(self, request):
        if request.match_info.get('token') != self.sec_tok:
            return web.Response(text='Not authorized', status=401)
        lid = request.match_info.get('lid')
        if self._set_todo(lid):
            return web.Response(text='Default list changed.')
        return web.Response(text='Invalid identifier.')

    async def add_todo(self, ctx):
        '''Add a todo in the default list.'''
        if self._todo_list is None:
            await ctx.send('There is no default list set. Please use /set todo')
        else:
            # TODO extract lid from parameters when adding cards NOT in TODO
            message = ctx.update.get('text').strip()
            if message.startswith('/'):
                space = message.index(' ')
                message = message[space:].lstrip()
            card = self._trello.create_card(self._todo_list, message)
            await ctx.send(f'Added TODO as card {card}')

    async def wl_org(self, ctx):
        """Whitelist organizations."""
        logging.info('Requested /wlo')
        # Get org IDS to whitelist
        oids = self._get_args(ctx)
        for oid in oids:
            self._trello.whitelist_org(oid)
        # TODO update data and jobs

    async def bl_org(self, ctx):
        """Blacklist organizations."""
        logging.info('Requested /blo')
        # Get org IDs to whitelist
        oids = self._get_args(ctx)
        for oid in oids:
            self._trello.blacklist_org(oid)
        # TODO  update data and jobs

    def _wlb(self, bids):
        """Whitelist boards by id."""
        if len(bids):
            for bid in bids:
                self._trello.whitelist_brd(bid)
            return True
        return False
        # TODO  update data and jobs

    def _blb(self, bids):
        """Blacklist boards by id."""
        if len(bids):
            for bid in bids:
                self._trello.blacklist_brd(bid)
            return True
        return False
        # TODO  update data and jobs

    async def wl_board(self, ctx):
        """Whitelist boards."""
        logging.info('Requested /wlb')
        bids = self._get_args(ctx)
        if self._wlb(bids[1:]):
            await ctx.send('Boards whitelisted successfully.')
        else:
            await self._list_boards(ctx)

    async def bl_board(self, ctx):
        """Blacklist boards."""
        logging.info('Requested /blb')
        bids = self._get_args(ctx)
        if self._blb(bids[1:]):
            await ctx.send('Boards blacklisted successfully.')
        else:
            await self._list_boards(ctx)

    # FIXME this is not upcoming, this is "uncompleted cards"
    async def upcoming_due(self, ctx):
        """Send user a list with upcoming cards."""
        logging.info('Requested /upcoming')
        # Check all cards for upcoming dues
        pdm, cdm = '*Past dues*:', '*Dues*:'
        async with await ctx.spawn(pdm) as pem, await ctx.spawn(cdm) as fem:
            # Fetch all the cards in whitelisted boards
            for b in self._trello.fetch_boards():
                if b.blacklisted:
                    continue
                print('Got board', b.name)
                for c in self._trello.fetch_cards(bid=b.id):
                    if c.dueComplete or c.due is None:
                        continue
                    if c.due < aware_now():
                        await pem.append(f'\n - {c}')
                    else:
                        await fem.append(f'\n - {c}')

        # Old code below
        if False:
            # Show upcoming cards
            for dd in self._dues:
                card = self._trello.get_card(dd)
                # print('Processing card', dd, self._dues[dd])
                # Past dues in a separated list
                if self._dues[dd] < aware_now():
                    #for c in self._dues[dd]:
                    await pem.append(f'\n - {card}')
                else:
                    #for c in self._dues[dd]:
                    await fem.append(f'\n - {card}')

    async def today_due(self, ctx):
        """Send user a list with cards due today."""
        logging.info('Requested /today')
        pdm, cdm = '*Past dues today*:', '*Due today*:'
        async with await ctx.spawn(pdm) as pem, await ctx.spawn(cdm) as fem:
            # Fetch all the cards in whitelisted boards and filter by day
            for b in self._trello.fetch_boards():
                if b.blacklisted:
                    continue
                for c in self._trello.fetch_cards(bid=b.id):
                    if c.dueComplete or c.due is None:
                        continue
                    # Divide past dues still missing today
                    now = aware_now()
                    if c.due.date() == now.date():
                        if c.due < now:
                            await pem.append(f'\n - {c}')
                        else:
                            await fem.append(f'\n - {c}')
            if False:
                #da_fare: leggere le card da trello e trovare tutte quelle pending
                for dd in self._dues:
                    # Skip past due
                    if dd.date() != aware_now().date():
                        continue
                    #for c in self._dues[dd]:
                    await em.append(f'\n - {c}')

    async def _update_boards_lists(self, aem, bem, stm):
        """Update the message with current board status."""
        # Functions to get URLs for white/black-list boards
        def wlb_url(b):
            return f'[WL]({self.url}/api/{self.sec_tok}/wlb/{b.id})'

        def blb_url(b):
            return f'[BL]({self.url}/api/{self.sec_tok}/blb/{b.id})'

        def lsl_url(b):
            return f'[lsl]({self.url}/api/{self.sec_tok}/lsl/{b.id})'

        # Set titles
        await aem.override('*Allowed boards*')
        await bem.override('*Not allowed boards*')
        await stm.override('*Status*: fetching data')
        await aem.flush()
        await bem.flush()
        await stm.flush()

        # Wait a second to make server happy
        await asyncio.sleep(1)

        # Fetch boards and update messages
        for b in self._trello.fetch_boards():
            if b.blacklisted:
                await bem.append(f'\n - {b} ({wlb_url(b)}) {lsl_url(b)}')
            else:
                await aem.append(f'\n - {b} ({blb_url(b)}) {lsl_url(b)}')
        # Write all data
        await aem.flush()
        await bem.flush()
        # Mark as done
        await asyncio.sleep(1)
        await stm.override(f'*Status*: Done.')
        await stm.flush()

    async def _list_boards(self, ctx):
        """Send two messages and fill them with wl/bl boards."""
        # List boards, blacklisted and not
        wait = 'Please wait...'
        async with await ctx.spawn(wait, quiet=self._quiet) as aem:
            async with await ctx.spawn(wait, quiet=self._quiet) as bem:
                async with await ctx.spawn(wait, quiet=self._quiet) as stm:
                    await self._update_boards_lists(aem, bem, stm)

    def _schedule_repeating_updates(self, ctx, job_queue):
        """(Re)schedule check_updates to be repeated."""
        if self._job_check is not None:
            # Stop previous check
            self._job_check.cancel()

        def _cb(*args):
            async def _coro(c, jq):
                await self.check_updates(c, jq)
                self._schedule_repeating_updates(c, jq)
            loop = asyncio.get_event_loop()
            loop.create_task(_coro(*args))

        delay = TrelloBot.update_int * 60
        self._job_check = job_queue.run_once(_cb, delay, ctx, job_queue)

    def _cancel_repeating_notifications(self):
        """Remove repeating notifications if there are any scheduled."""
        if self._job_notif is not None:
            self._job_notif.cancel()

    def _schedule_repeating_notifications(self, ctx, job_queue):
        """(Re)schedule check_notifications to be repeated."""
        logging.info('Scheduling repeating notifications')
        self._cancel_repeating_notifications()

        def _cb(*args):
            async def _coro(c, jq):
                await self.check_notifications(c, jq)
                self._schedule_repeating_notifications(c, jq)
            loop = asyncio.get_event_loop()
            loop.create_task(_coro(*args))

        delay = TrelloBot.notify_int * 3600
        self._job_notif = job_queue.run_once(_cb, delay, ctx, job_queue)

    def _get_args(self, ctx):
        """Return a list of arguments from update message text."""
        logging.info('Getting argum')
        return ctx.update.get('text').strip().split()

    async def start(self, ctx, job_queue):
        """Start the bot, schedule tasks and printing welcome message."""
        # Welcome message
        await ctx.send(
            f'*Welcome!*\n'
            f'TrelloBot will now make your life better. ',
            quiet=self._quiet,
        )

        # List boards and their blacklistedness
        await self._list_boards(ctx)
        # Update due dates
        await self._update(ctx, job_queue, False)
        # Warn user about planned activity
        await ctx.send(f'Refreshing every {TrelloBot.update_int} mins',
                       quiet=self._quiet)

        # Start repeated job
        self._schedule_repeating_updates(ctx, job_queue)
        self._schedule_repeating_notifications(ctx, job_queue)
        self.started = True

    # async def circles(self, ctx):
    #     """Generate circles and send it."""
    #     await ctx.sendPhoto()

    async def dispatch(self, update):
        """Dispatch chat messages."""
        logging.info(f'Got a message to dispatch {update}')
        for ctx in await security.security_check(self.bot, update):
            print('Security check passed')

            # Dispatch based on first token received
            text = update.get('text')
            if not text:
                logging.info(f'Message does not contain text! Skipping')
                await ctx.send("I'm unable to understand non-text messages")
                continue

            jq = JobQueue()

            commands = {
                '/start': (self.start, True),  # Needs job queue
                '/update': (self.rescan_updates, True),  # Needs job queue
                '/set': (self.preferences, True),  # Needs job queue
                # "Always-fresh" commands: they read directly from trello
                ('/upcoming', '/upc'): self.upcoming_due,
                '/today': self.today_due,
                # '/add': self.add_card,
                '/todo': self.add_todo,  # TODO fallback per i messaggi non compresi
                '/lso': self.lso,
                '/lsb': self.lsb,
                '/lsl': self.lsl,
                '/wlo': self.wl_org,
                '/blo': self.bl_org,
                '/wlb': self.wl_board,
                '/blb': self.bl_board,
                # '/circles': self.circles,
            }

            token = text.split()[0]
            if token == '/help':
                await ctx.send('Accepted commands:\nstart, update, set, upc, today, todo, lso, lsb, lsl, wlo, blo, wlb, wlb')
                return
            for cmd, fun in commands.items():
                if isinstance(fun, tuple):
                    fun, pass_job_queue = fun
                else:
                    pass_job_queue = False
                if token == cmd or isinstance(cmd, tuple) and token in cmd:
                    if pass_job_queue:
                        await fun(ctx, jq)
                    else:
                        await fun(ctx)
                    break
            else:
                if TrelloBot.todo_as_default and not token.startswith('/'):
                    # Add todo as default
                    await self.add_todo(ctx)
                else:
                    await ctx.sendSticker('CAADBAADKwADRHraBbFYz9aWfY9kAg')
                    await ctx.send('I did not understand!')

    async def web_wlb(self, request):
        if request.match_info.get('token') != self.sec_tok:
            return web.Response(text='Not authorized', status=401)
        bid = request.match_info.get('bid')
        if self._wlb([bid]):
            return web.Response(text='Board whitedlisted successfully.')
        return web.Response(text='Could not whitelist board.')

    async def web_blb(self, request):
        if request.match_info.get('token') != self.sec_tok:
            return web.Response(text='Not authorized', status=401)
        bid = request.match_info.get('bid')
        if self._blb([bid]):
            return web.Response(text='Board blacklisted successfully.')
        return web.Response(text='Could not blacklist board.')

    def run_async(self, bot_key, bot_url):
        """Start the bot using asyncio."""
        # Telegram served interface, imported here to avoid the cration
        # of a main loop when importing the current module (bot.py)
        import telepot
        from telepot.aio.loop import MessageLoop

        logging.info('Starting async bot')
        self.bot = telepot.aio.Bot(bot_key)
        handlers = {
            'chat': self.dispatch,
        }

        # Generate a security token
        self.sec_tok = secrets.token_urlsafe(25)
        self.url = bot_url

        app = web.Application()
        app.router.add_get('/api/{token}/set/todo/{lid}', self.web_set_todo)
        app.router.add_get('/api/{token}/wlb/{bid}', self.web_wlb)
        app.router.add_get('/api/{token}/blb/{bid}', self.web_blb)
        app.router.add_get('/api/{token}/lsl/{bid}', self.web_lsl)
        loop = asyncio.get_event_loop()
        loop.create_task(MessageLoop(self.bot, handlers).run_forever())
        web.run_app(app)
