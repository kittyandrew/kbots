import argparse
import asyncio
import logging
import os
import platform
import sys
from collections.abc import Awaitable, Callable
from configparser import ConfigParser
from dataclasses import dataclass
from functools import cached_property

import sentry_sdk
import telethon

ModuleInit = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class TelegramAccount:
    api_hash: str
    api_id: int
    session_path: str

    @classmethod
    def from_config(cls, config: ConfigParser, *, session_key: str):
        return cls(
            api_hash=config.get("telegram", "api_hash").strip(),
            api_id=config.getint("telegram", "api_id"),
            session_path=config.get("general", session_key).strip(),
        )

    def __post_init__(self):
        if not self.api_hash:
            raise ValueError("telegram.api_hash must not be empty")
        if self.api_id <= 0:
            raise ValueError("telegram.api_id must be positive")
        if not self.session_path:
            raise ValueError("telegram session path must not be empty")

    @cached_property
    def client(self):
        return telethon.TelegramClient(session=self.session_path, api_hash=self.api_hash, api_id=self.api_id)

    async def load(self):
        """Load an existing session file and verify the account is authorized."""
        await self.client.connect()

        if await self.client.is_user_authorized():
            return self.client

        clean = await self.client.log_out()
        if not clean:
            raise RuntimeError("Telegram log_out() returned False; session cleanup failed")
        return None

    async def login(self, *, bot_token: str | None = None):
        """Interactive login: prompts for phone/code, or uses bot_token when provided."""
        await self.client.start(bot_token=bot_token)
        return self.client


def run_bot(
    cpath: str,
    *,
    module_init: ModuleInit,
    logger_name: str,
    login: bool = False,
    user_login: bool = False,
    needs_user: bool = False,
    bot_login_uses_token: bool = False,
):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    config = ConfigParser()
    config.read(cpath)

    async def _main(config: ConfigParser):
        debug = config.getboolean("general", "debug")

        logging.basicConfig(
            format="%(asctime)s - %(filename)s - %(levelname)s - %(message)s",
            datefmt="%d-%b-%y %H:%M:%S",
            level=logging.DEBUG if debug else logging.INFO,
        )
        logging.getLogger("telethon").setLevel(logging.WARNING)
        logger = logging.getLogger(logger_name)

        sentry_sdk.init(
            traces_sample_rate=1.0,
            environment=os.environ.get("SENTRY_ENVIRONMENT", os.environ.get("HOSTNAME", platform.node())),
            release=os.environ.get("SENTRY_RELEASE", os.environ.get("GIT_SHA", "dev")),
        )

        context = dict(logger=logger, config=config)
        context["storage"] = context

        bot_token = config.get("telegram", "token", fallback=None) if login and bot_login_uses_token else None
        account = TelegramAccount.from_config(config, session_key="session")

        if login:
            await account.login(bot_token=bot_token)
            return

        if not os.path.exists(account.session_path):
            print(f"Session file '{account.session_path}' is missing!")
            sys.exit(1)

        client = await account.load()
        if client is None:
            print(f"Session '{account.session_path}' is expired or invalid. Re-run with --login to authenticate.")
            sys.exit(1)
        context["client"] = client

        if needs_user:
            user_account = TelegramAccount.from_config(config, session_key="user_session")

            if user_login:
                await user_account.login()
                return

            if not os.path.exists(user_account.session_path):
                print(f"Session file '{user_account.session_path}' is missing!")
                sys.exit(1)

            user = await user_account.load()
            if user is None:
                print(f"Session '{user_account.session_path}' is expired or invalid. Re-run with --user-login to authenticate.")
                sys.exit(1)
            context["user"] = user

        await module_init(**context)
        logger.info("Initiation completed ...")

    loop.run_until_complete(_main(config))

    if login or user_login:
        sentry_sdk.flush(timeout=2)
        loop.close()
        return

    try:
        loop.run_forever()
    finally:
        sentry_sdk.flush(timeout=2)
        loop.stop()
        loop.close()


def main_cli(
    *,
    module_init: ModuleInit,
    logger_name: str,
    needs_user: bool = False,
    bot_login_uses_token: bool = False,
):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to the config file.",
    )
    parser.add_argument(
        "--login",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Instead of starting the application, log in.",
    )
    if needs_user:
        parser.add_argument(
            "--user-login",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Instead of starting the application, log your user account in.",
        )
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"Config file doesn't exist [path '{args.config}']!")
        sys.exit(1)

    return run_bot(
        args.config,
        module_init=module_init,
        logger_name=logger_name,
        login=args.login,
        user_login=getattr(args, "user_login", False),
        needs_user=needs_user,
        bot_login_uses_token=bot_login_uses_token,
    )
