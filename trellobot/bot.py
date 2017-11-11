"s""Implementation of the actual bot."""

# Telegram served interface
import telepot
from telepot.aio.loop import MessageLoop

# Web served interface
import asyncio
from aiohttp import web

# Some logging
import logging
import secrets

from trellobot.messaging import Messenger
from trellobot.security import security_check
from trellobot.trello import TrelloManager

import humanize
from datetime import datetime
from datetime import timezone

from collections import Counter


def aware_now():
    """Return TZ-aware now."""
    return datetime.now(timezone.utc)


def tznaive(d):
    """Return where a datetime object is TZ naive or not."""
    return d.tzinfo is None or d.tzinfo.utcoffset(d) is None


class JobQueue:
    """Manages async jobs."""

    def run_once(self, callback, delay, *args):
        """Schedule a callback to be executed after given delay (seconds)."""
        loop = asyncio.get_event_loop()
        # TODO use functool partial to send kwargs
        return loop.call_later(delay, callback, *args)


class TrelloBot:
    """Bot to make Trello perfect."""

    update_int = 10  # Update interval in minutes
    notify_int = 2  # Notification interval in hours
    past_due_notif_limit = 24  # Time limit for past due to consider, in hours
    due_soon_notif_limit = 1  # Time limit for due soon to consider time limit, in hours

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
        delay = (card.due - aware_now()).total_seconds()
        # If due date is past, we might handle it anyway
        if delay < 0:
            # Notify: you had a non-completed card in the last 24 hours!
            if delay > -3600 * TrelloBot.past_due_notif_limit and not card.dueComplete:
                logging.info(f'Non-sched card with recently past due {card}')
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
                count = await self._check_due(msg, job_queue)
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

    def daily_report(self, bot, job):
        """Send a daily report about tasks."""
        # TODO use security_check
        # for ctx in security_check(bot, update):
        pass
        # TODO list cards due next 24 hours
        # TODO list cards completed in the last 24 hours

    async def ls(self, ctx):
        """List the requested resources from Trello."""
        logging.info('Requested /ls')
        target = self._get_args(ctx)
        # List organizations if nothing was specified
        if len(target) == 1:
            async with await ctx.spawn('*Organizations:*\n') as msg:
                later = []
                for o in self._trello.fetch_orgs():
                    if o.blacklisted:
                        later.append(f' - {o.name}')
                    else:
                        await msg.append(f' - {o.name}\n')
                # Print blacklisted organizations after
                if later:
                    await msg.append('Blacklisted:\n')
                    await msg.append('\n'.join(later))
        elif len(target) == 2 and target[1] in self._trello.org_names():
            org = target[1]
            async with await ctx.spawn(f'*Boards in org* {org}:\n') as msg:
                later = []
                for b in self._trello.fetch_boards(org):
                    if b.blacklisted:
                        later.append(f' - {b.name}')
                    else:
                        await msg.append(f' - {b.name}\n')
                if later:
                    await msg.append('Blacklisted:\n')
                    await msg.append('\n'.join(later))
        else:
            await ctx.send('Sorry, I cannot list anything else right now.')

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
        except Exception:
            # Whatever goes wrong
            # TODO display current values as well
            await ctx.send(
                f'*Settings help*\n'
                f'/set update interval [0.3:{60*24}] _update interval (mins)_\n'
                f'/set notification interval  [on|off|0.1:24] _notification interval (hours)_\n'
                f'/set quiet _make bot quieter_\n'
                f'/set verbose _make bot verbose_\n'
            )

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

    async def upcoming_due(self, ctx):
        """Send user a list with upcoming cards."""
        logging.info('Requested /upcoming')
        # Check all cards for upcoming dues
        pdm, cdm = '*Past dues*:', '*Dues*:'
        async with await ctx.spawn(pdm) as pem, await ctx.spawn(cdm) as fem:
            # Show upcoming cards
            for dd in self._dues:
                # Past dues in a separated list
                if dd < aware_now():
                    for c in self._dues[dd]:
                        await pem.append(f'\n - {c}')
                else:
                    for c in self._dues[dd]:
                        await fem.append(f'\n - {c}')

    async def today_due(self, ctx):
        """Send user a list with cards due today."""
        logging.info('Requested /today')
        async with await ctx.spawn('*Due today*') as em:
            # Show upcoming cards
            for dd in self._dues:
                # Skip past due
                if dd.date() != aware_now().date():
                    continue
                for c in self._dues[dd]:
                    await em.append(f'\n - {c}')

    def demo(self, bot, update):
        """Demo buttons and callbacks."""
        # If security check passes
        for ctx in security_check(bot, update):
            ctx.send(f'A *markdown* message :)', keyboard=[
                    [
                        {'text': 'Greetings', 'callback_data': 'puny human'},
                    ],
                    [
                        {'text': 'Adieu', 'callback_data': 'my friend'},
                    ],
                ]
            )

    async def _update_boards_lists(self, aem, bem, stm):
        """Update the message with current board status."""
        # Functions to get URLs for white/black-list boards
        def wlb_url(b):
            return f'[WL]({self.url}/api/{self.sec_tok}/wlb/{b.id})'

        def blb_url(b):
            return f'[BL]({self.url}/api/{self.sec_tok}/blb/{b.id})'

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
                await bem.append(f'\n - {b} ({wlb_url(b)})')
            else:
                await aem.append(f'\n - {b} ({blb_url(b)})')

        await asyncio.sleep(1)

        await stm.override(f'*Status*: Done.')

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

    def buttons(self, bot, update):
        """Handle buttons callbacks."""
        query = update.callback_query
        # Answer with a notification, without touching messages
        bot.answerCallbackQuery(
            query.id,
            text='Baccalà baccaqua',  # A message to be sent to client
            show_alert=True,  # If True, user gets a modal message
        )
        print(query.message)
        with Messenger.from_query(bot, query) as msg:
            msg.override(
                'YAY FUNZIONA YEASH!',
                keyboard=[
                    [
                        {'text': 'You are', 'callback_data': 'dead to me'},
                    ],
                    [
                        {'text': 'My friend', 'callback_data': 'I hate you!'},
                    ],
                ]
            )

    def errors(self, bot, update, error):
        logging.error(f'Got an error {error}')

    async def dispatch(self, update):
        """Dispatch chat messages."""
        logging.info(f'Got a message to dispatch {update}')
        for ctx in await security_check(self.bot, update):
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
                ('/upcoming', '/upc'): self.upcoming_due,
                ('/today', '/tod'): self.today_due,
                '/ls': self.ls,
                '/wlo': self.wl_org,
                '/blo': self.bl_org,
                '/wlb': self.wl_board,
                '/blb': self.bl_board,
            }

            token = text.split()[0]
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
                await ctx.sendSticker('CAADBAADKwADRHraBbFYz9aWfY9kAg')
                await ctx.send('I did not understand')

    async def dispatch_cb(self, update):
        """Dispatch call back updates."""
        print('Bar', update)

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
        logging.info('Starting async bot')
        self.bot = telepot.aio.Bot(bot_key)
        handlers = {
            'chat': self.dispatch,
            'callback_query': self.dispatch_cb,
        }

        # Generate a security token
        self.sec_tok = secrets.token_urlsafe(25)
        self.url = bot_url

        app = web.Application()
        app.router.add_get('/api/{token}/wlb/{bid}', self.web_wlb)
        app.router.add_get('/api/{token}/blb/{bid}', self.web_blb)
        loop = asyncio.get_event_loop()
        loop.create_task(MessageLoop(self.bot, handlers).run_forever())
        web.run_app(app)
