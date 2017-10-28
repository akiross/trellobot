"""Implementation of the actual bot."""

# Telegram bot API
from telegram.ext import Updater
from telegram.ext import CommandHandler
from telegram.ext import CallbackQueryHandler

# Some logging
import logging

from trellobot.messaging import Messenger
from trellobot.security import security_check
from trellobot.trello import TrelloManager

import humanize
from datetime import datetime
from datetime import timezone

from collections import Counter
from contextlib import ExitStack


def aware_now():
    """Return TZ-aware now."""
    return datetime.now(timezone.utc)


def tznaive(d):
    """Return where a datetime object is TZ naive or not."""
    return d.tzinfo is None or d.tzinfo.utcoffset(d) is None


class TrelloBot:
    """Bot to make Trello perfect."""

    check_int = 10  # Check interval in minutes

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
        # Try to be as quiet as possible (remember: user can mute it)
        self._quiet = True

        self._trello = TrelloManager(
            api_key=trello_key,
            api_secret=trello_secret,
            token=trello_token,
        )

    # def _schedule_notifications(self):
    #    """Schedule notifications."""
    #    """
    #    È importante che si possano cancellare e rischedulare i job
    #    quando si rinfresca l'elenco dei due date. I job pendenti devono
    #    essere rimossi e rischedulati.
    #
    #    Usare job_queue.run_once(when=datetime.datetime) per specificare
    #    precisamente quando devo notificare una due date.
    #
    #    Usare job_queue.run_daily(time=datetime.time) per specificare quando
    #    bisogna notificare i task rimanenti della giornata (e.g. una volta al
    #    mattino e una volta alla sera).
    #    """

    def _card_notification(self, bot, job):
        """Notify that a card is due shortly."""
        ctx, card = job.context
        when = aware_now() - card.due
        ctx.send(f'Card {card} due {humanize.naturaltime(when)}')

    def _schedule_due(self, card, ctx, job_queue):
        """Schedule a job for due card, return True if actually enqueued."""
        # We are using time-aware dates, telegram API isn't:
        # convert to delay instead of using directly a datetime
        delay = (card.due - aware_now()).total_seconds()
        # If due date is past, we might handle it anyway
        if delay < 0:
            # Notify: you had a non-completed card in the last 24 hours!
            if delay > -3600*24 and not card.dueComplete:
                logging.debug(f'Non-sched card with recently past due {card}')
                ctx.send(f'Card was due in the last 24 hours! {card}')
            else:
                logging.debug(f'Non-sched card with far past due {card}')
            return False
        else:
            # In case of positive delay, we want to notify some time *before*
            # the actual due date
            delay -= 3600
            # If there is no time, notify immediately!
            if delay < 0:
                logging.debug(f'Non-scheduling card due soon {card}')
                ctx.send(f'Card is due in less than 1 hour! {card}')
                return False
            else:
                logging.debug(f'Scheduling card due in future {card}')
        # Schedule a notification and save the job for this card
        self._jobs[card.id] = job_queue.run_once(
            self._card_notification,
            when=delay,
            context=(ctx, card))
        self._dues[card.id] = card.due  # Save original due date
        return True

    def _reschedule_due(self, card, ctx, job_queue):
        """Reschedule a job for due card."""
        self._unschedule_due(card.id, ctx, job_queue)
        self._schedule_due(card, ctx, job_queue)

    def _unschedule_due(self, cid, ctx, job_queue):
        """Unschedule a job previously set for due card."""
        self._jobs[cid].schedule_removal()
        del self._jobs[cid]
        del self._dues[cid]  # Removed associated due date

    def _update_due(self, bid, ctx, jq):
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
                # Card has due date set
                if c.id not in self._jobs:
                    # Card is not scheduled: it could be new or completed
                    if c.dueComplete:
                        # If card is complete, ignore it
                        count['ignored'] += 1
                    elif self._schedule_due(c, ctx, jq):
                        # Card were actually accepted for scheduling, likely
                        # because due date is in the future
                        count['scheduled'] += 1
                    else:
                        # Card was not scheduled, maybe for due date in past
                        # or because notification was sent immediately
                        count['ignored'] += 1
                else:
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
        return count, scanned

    def _check_due(self, bot, ctx, job_queue):
        """Rebuild the dictionary of due dates."""
        # Iterate all the boards
        count = Counter()
        scanned = set()
        for b in self._trello.fetch_boards():
            if not b.blacklisted:
                c, s = self._update_due(b.id, ctx, job_queue)
                count += c
                scanned.update(s)
        # Check for removed cards (TODO get notifications from trello?)
        saved = set(self._dues.keys())
        for cid in saved - scanned:
            self._unschedule_due(cid, ctx, job_queue)
            count['deleted'] += 1
        # Return counter
        return count

    def _update(self, bot, update, job_queue, quiet):
        """Rescan cards tracking due dates."""
        for ctx in security_check(bot, update):
            if quiet:
                self._check_due(bot, ctx, job_queue)
            else:
                stm = '*Status*: Scanning for updates...'
                with ctx.spawn(stm) as msg:
                    # Get data, caching them
                    count = self._check_due(bot, msg, job_queue)
                    # n = len(list(self._trello.fetch_data()))
                    msg.override(f'*Status*: Done. ' + self._report(count))

    def check_updates(self, bot, job):
        """Check if new threads are present since last check."""
        logging.info('JOB: checking updates')
        update, job_queue = job.context
        self._update(bot, update, job_queue, self._quiet)

    def _report(self, count):
        """Produce a report regarding count."""
        return ', '.join([f'{v} cards {k}' for k, v in count.items()])

    def rescan_updates(self, bot, update, job_queue):
        """Rescan cards tracking due dates."""
        # User requested an explicit update, so disable quiet mode
        self._update(bot, update, job_queue, False)

    def daily_report(self, bot, job):
        """Send a daily report about tasks."""
        # TODO use security_check
        # for ctx in security_check(bot, update):
        pass
        # TODO list cards due next 24 hours
        # TODO list cards completed in the last 24 hours

    def ls(self, bot, update):
        """List the requested resources from Trello."""
        logging.info('Requested /ls')
        for ctx in security_check(bot, update):
            ctx.send(f'ECHO: {update.message}')
            target = update.message.text.strip().split()
            # List organizations if nothing was specified
            if len(target) == 1:
                with ctx.spawn('Listing Organizations:\n') as msg:
                    later = []
                    for o in self._trello.fetch_orgs():
                        if o.blacklisted:
                            later.append(f' - {o.name}')
                        else:
                            msg.append(f' - {o.name}\n')
                    # Print blacklisted organizations after
                    if later:
                        msg.append('Blacklisted:\n')
                        msg.append('\n'.join(later))

            elif len(target) == 2 and target[1] in self._trello.org_names():
                org = target[1]
                with ctx.spawn(f'Listing Boards in org {org}:\n') as msg:
                    later = []
                    for b, bl in self._trello.fetch_boards(org):
                        if bl:
                            later.append(f' - {b.name}')
                        else:
                            msg.append(f' - {b.name}\n')
                    if later:
                        msg.append('Blacklisted:\n')
                        msg.append('\n'.join(later))
            else:
                ctx.send('Sorry, I cannot list anything else right now.')

    def preferences(self, bot, update, job_queue):
        """Process user preferences."""
        for ctx in security_check(bot, update):
            tokens = update.message.text.split()
            try:
                if tokens[1] == 'interval':
                    t = float(tokens[2])  # Index and value can fail
                    # Bound the maximum check interval in [30 seconds, 1 day]
                    TrelloBot.check_int = min(max(t, 0.3), 60 * 24)
                    ctx.send(f'Interval set to {TrelloBot.check_int} minutes')
                    self._schedule_repeating_updates(update, job_queue)
                    # Accept changes
                elif tokens[1] == 'quiet':
                    self._quiet = True
                    ctx.send('I will be quieter now')
                elif tokens[1] == 'verbose':
                    self._quiet = False
                    ctx.send('I will talk a bit more now')
            except Exception:
                # Whatever goes wrong
                # TODO display current values as well
                ctx.send(
                    f'*Settings help*\n'
                    f'/set interval [0.3:{60*24}] _update interval_\n'
                    f'/set quiet _make bot quieter_\n'
                    f'/set verbose _make bot verbose_\n'
                )

    def wl_org(self, bot, update):
        """Whitelist organizations."""
        logging.info('Requested /wlo')
        for ctx in security_check(bot, update):
            # Get org IDS to whitelist
            oids = update.message.text.strip().split()
            for oid in oids:
                self._trello.whitelist_org(oid)
        # TODO update data and jobs

    def bl_org(self, bot, update):
        """Blacklist organizations."""
        logging.info('Requested /blo')
        for ctx in security_check(bot, update):
            # Get org IDs to whitelist
            oids = update.message.text.strip().split()
            for oid in oids:
                self._trello.blacklist_org(oid)
        # TODO  update data and jobs

    def wl_board(self, bot, update):
        """Whitelist boards."""
        logging.info('Requested /wlb')
        for ctx in security_check(bot, update):
            bids = update.message.text.strip().split()
            if len(bids) > 1:
                for bid in bids[1:]:
                    self._trello.whitelist_brd(bid)
                ctx.send('Boards whitelisted successfully.')
            else:
                self._list_boards(ctx)

        # TODO  update data and jobs

    def bl_board(self, bot, update):
        """Blacklist boards."""
        logging.info('Requested /blb')
        for ctx in security_check(bot, update):
            bids = update.message.text.strip().split()
            if len(bids) > 1:
                for bid in bids:
                    self._trello.blacklist_brd(bid)
                ctx.send('Boards blacklisted successfully.')
            else:
                self._list_boards(ctx)
        # TODO  update data and jobs

    def upcoming_due(self, bot, update):
        """Send user a list with upcoming cards."""
        logging.info('Requested /upcoming')
        for ctx in security_check(bot, update):
            logging.info('Authorized user requested upcoming dues.')
            # Check if we loaded due cards
            if not hasattr(self, '_dues'):
                ctx.send('No data fetched, did you start?')
                return
            # Check all cards for upcoming dues
            pdm, cdm = '*Past dues*:', '*Dues*:'
            with ctx.spawn(pdm) as pem, ctx.spawn(cdm) as fem:
                # Show upcoming cards
                for dd in self._dues:
                    # Past dues in a separated list
                    if dd < aware_now():
                        for c in self._dues[dd]:
                            pem.append(f'\n - {c}')
                    else:
                        for c in self._dues[dd]:
                            fem.append(f'\n - {c}')

    def today_due(self, bot, update):
        """Send user a list with cards due today."""
        for ctx in security_check(bot, update):
            with ctx.spawn('*Due today*') as em:
                # Show upcoming cards
                for dd in self._dues:
                    # Skip past due
                    if dd.date() != aware_now().date():
                        continue
                    for c in self._dues[dd]:
                        em.append(f'\n - {c}')

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

    def _list_boards(self, ctx):
        """Send two messages and fill them with wl/bl boards."""
        # List boards, blacklisted and not
        abm = '*Allowed boards*'
        bbm = '*Not allowed boards*'
        stm = '*Status*: fetching data'
        with ExitStack() as es:
            # Setup context managers
            aem = es.enter_context(ctx.spawn(abm, quiet=self._quiet))
            bem = es.enter_context(ctx.spawn(bbm, quiet=self._quiet))
            stm = es.enter_context(ctx.spawn(stm, quiet=self._quiet))
            # Fetch boards and update messages
            for b in self._trello.fetch_boards():
                if b.blacklisted:
                    bem.append(f'\n - {b} {b.id}')
                else:
                    aem.append(f'\n - {b} {b.id}')
            stm.override(f'*Status*: Done.')

    def _schedule_repeating_updates(self, update, job_queue):
        """(Re)schedule check_updates to be repeated."""
        if self._job_check is not None:
            # Stop previous check
            self._job_check.schedule_removal()

        self._job_check = job_queue.run_repeating(
            self.check_updates,
            TrelloBot.check_int * 60.0,
            context=(update, job_queue),
        )

    def start(self, bot, update, job_queue):
        """Start the bot, schedule tasks and printing welcome message."""
        logging.info(f'Requested /start from user {update.message.chat_id}')

        # If security check passes
        for ctx in security_check(bot, update):
            # self.last_check = aware_now()
            # Welcome message
            ctx.send(
                f'*Welcome!*\n'
                f'TrelloBot will now make your life better. ',
                quiet=self._quiet,
            )

            # List boards and their blacklistedness
            self._list_boards(ctx)
            self._update(bot, update, job_queue, False)

            ctx.send(f'Refreshing every {TrelloBot.check_int} mins',
                     quiet=self._quiet)

            # Start repeated job
            self._schedule_repeating_updates(update, job_queue)
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
        # Edit message connected to keyboard
        # Keyboard is removed
        # bot.edit_message_text(
        #     text="Selected option: {}".format(query.data),
        #     chat_id=query.message.chat_id,
        #     message_id=query.message.message_id,
        #
        # )

    def run_bot(self, bot_key):
        """Start the bot, register handlers, etc."""
        # Setup bot
        updater = Updater(token=bot_key)

        disp = updater.dispatcher

        # Handler for buttons
        disp.add_handler(CallbackQueryHandler(self.buttons))
        disp.add_handler(CommandHandler('start',
                                        self.start,
                                        pass_job_queue=True))
        disp.add_handler(CommandHandler('update',
                                        self.rescan_updates,
                                        pass_job_queue=True))
        # disp.add_handler(CommandHandler('ls', self.ls))
        # Blacklist management
        disp.add_handler(CommandHandler('wlo', self.wl_org))
        disp.add_handler(CommandHandler('blo', self.bl_org))
        disp.add_handler(CommandHandler('wlb', self.wl_board))
        disp.add_handler(CommandHandler('blb', self.bl_board))
        disp.add_handler(CommandHandler('set',
                                        self.preferences,
                                        pass_job_queue=True))
        # disp.add_handler(CommandHandler(['upcoming', 'upc', 'up', 'u'],
        #                                self.upcoming_due))
        # disp.add_handler(CommandHandler(['today', 'tod', 't'],
        #                                self.today_due))

        updater.start_polling()
