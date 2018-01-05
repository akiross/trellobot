# TrelloBot

TrelloBot is a Telegram bot that aims to help with Trello. Specifically:

 - improves notification by
   - notifying every day with the cards due in that day
   - notifying upcoming cards 1h/30m before due date
 - improves management of tasks by
   - listing due and past-due cards of selected boards/organizations
   - listing upcoming cards and cards due today
   - quickly adding a new card in a (predefined) list
   - quickly marking due cards as done

## Usage

To use the bot, you need to get Trello API keys. Right now, 1 bot = 1 user, but
support for many users is planned. A Dockerfile is included to make deploy fun.

### Usage: getting it running

First things first:

 - get trello key, secret and token, and place them in `key.txt`, `secret.txt`
   and `token.txt`, doing the necessary authentication steps.
 - get a telegram bot key from botfather, and place it in `bot.txt`
 - get your user ID in telegram from @userinfobot and save it in `allowed.txt`

Those things are necessary to use this bot, it's pointless to continue without.

Next, you'll need to retrieve the source code and run it in Docker.

    git clone https://github.com/akiross/trellobot.git

This will create a `trellobot` directory in your CWD. Copy the key files you
created earlier in this directory. TrelloBot will read the files from there.

Next, you'll have to build the Docker image and run it. Should be easy:

	# Enter source directory, containing your keys
    cd trellobot

	# Create docker image using current Dockerfile
	docker build -t trellobot .

	# Run the bot as a daemon on your domain example.com on port 1234
	docker run -d -p 1234:8080 -e TRELLOBOT_URL=example.com:1234 trellobot

If everything is fine (and it should be), you'll start to see logging and your
bot will be available. If it's not working, double-check these steps and, if
still not working, open an issue on github with lots of details.

### Usage: actual usage of the bot

After `/start`-ing the bot, you should get a welcome message and few messages.

One message is the list of **allowed boards** (empty at the beginning) and the
next is the list of **not allowed boards**. Right now, you have to whitelist
all the boards you need to get notifications for.

Commands for blacklisting and whitelisting boards are `/blb` and `/wlb`.

Pick the ID of the board(s) you wish to whitelist and do it:

    /wlb 1a2b3c4c...  # you can pass many boards at once

After that, update the cards:

    /update

This command will check for cards in the whitelisted boards and setup timers
and will notify you when a card is due. The mechanism of its working is
somewhat complex and it should keep track of new cards, deleted cards,
completed dues, etc. After an update, you should see a message stating how many
cards were scheduled for notifications, unscheduled (when due is removed), 
completed (since the last update) or ignored (if they are completed, or if they
are cards without a due date).

The bot will periodically check for new due dates and will update itself,
sending you a message if a card is due in less than **1 hour**.

If, when updatind, it finds unchecked-cards with less than 24 hours past due
or if it find cards within 1 hour from their due date, it will notify you
immediately. This behavior might change sensibly in future.

For now, just use the bot in this way and ignore other commands. They might be
broken or incomplete, but I'm working on them.

## Hacking

TrelloBot is written in Python 3.6 and uses python-telegram-bot to handle the
Telegram side of things. The Trello side of things is based on py-trello, but
being not-really-maintained, only authentication and ajax querying functions
are used. py.test is used for testing.

Development happens in **devel** branch, while **master** contains only stable
releases deemed "ok for usage". Do not expect code in devel to work.

##  TODO

Here's a list of things to do, possibly sorted by priority.

 - [ ] Do not spam past dues.
 - [ ] One or two daily reminders of due cards and recently past dues.
 - [ ] Customizable 
 - [ ] Have decent testing of the bot, mocking/faking trello and telegram.
 - [X] Quick-add of cards with a default list if none is specified.
 - [ ] Instead of just blacklisting boards and organizations, the bot should
       respect user subscriptions to list, cards and boards (get due
	   notifications for every subscribed board/list/card and every card in
	   subscribed list/board unless unsubscribed). Eventually, WL and BL shall
	   be removed.
 - [ ] Allow multiple users at once (keys asked on start).
 - [ ] Support custom fields to make bot location-aware and other nice things.
