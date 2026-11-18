# Telegram Lead Monitor

A small Russian-language Telegram lead finder. It watches only the public channels/groups explicitly listed in `SOURCES`, filters likely requests for websites/landing pages, and sends matches to your Telegram bot.

## Important limitations

- This does **not** search every public Telegram chat. Telegram does not provide a universal feed of all public messages. Only sources accessible to the Telegram user account and listed in `SOURCES` are monitored.
- This version uses a Telegram **user** session (Telethon), not just the bot token. Keep `TELEGRAM_SESSION_STRING` private: it can grant access to your Telegram account. Never commit it to GitHub or share it in chat.
- Render's free web services can spin down after inactivity, so this is a test deployment and is **not guaranteed to monitor continuously** on the free plan.
- Check Telegram's terms and each community's rules. Monitor only public sources you are allowed to access.

## Render environment variables

Set these in Render → your service → Environment:

- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`: obtain them at https://my.telegram.org (API development tools).
- `TELEGRAM_SESSION_STRING`: generate locally with Telethon StringSession while signed into your own Telegram account. Do not send it to anyone.
- `BOT_TOKEN`: token from @BotFather. Keep it secret.
- `CHAT_ID`: your private chat ID with the bot. Open the bot and press Start first.
- `SOURCES`: comma-separated public channel/group usernames, without @, e.g. `public_channel,another_group`.

## Deploy

1. In Render choose **New → Blueprint** and connect this GitHub repository.
2. Render reads `render.yaml`; create the service.
3. Add all environment variables above in the Render dashboard.
4. Deploy and check the service logs.

## Security

Never put API hashes, session strings, bot tokens, passwords, or login codes in source files, GitHub, screenshots, or chat messages. If a token/session is exposed, revoke it immediately.
