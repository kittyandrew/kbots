import telethon


class BadAccountError(Exception):
    """Raised when a session file exists but the account is not authorized."""


class TGSpawner:
    def __init__(self, tg_api_hash: str, tg_api_id: int, path: str, logger=None):
        self.tg_api_hash = tg_api_hash
        self.tg_api_id = tg_api_id
        self.path = path
        self.logger = logger

    async def load_account(self):
        """Load an existing session file and verify the account is authorized."""
        client = telethon.TelegramClient(
            session=self.path,
            api_hash=self.tg_api_hash,
            api_id=self.tg_api_id,
        )
        await client.connect()

        if not await client.is_user_authorized():
            clean = await client.log_out()
            if not clean:
                raise RuntimeError("Telegram log_out() returned False; session cleanup failed")
            raise BadAccountError

        return client

    async def login(self, **kwargs):
        """Interactive login: prompts for phone/code, or uses bot_token when provided."""
        client = telethon.TelegramClient(
            session=self.path,
            api_hash=self.tg_api_hash,
            api_id=self.tg_api_id,
        )
        await client.start(**kwargs)
        return client
