import argparse
import asyncio
import logging
import os
import platform
import sys
from collections.abc import Awaitable, Callable
from configparser import ConfigParser

import sentry_sdk

from .new_account import BadAccountError, TGSpawner

ModuleInit = Callable[..., Awaitable[None]]


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

        api_hash = config.get("telegram", "api_hash")
        api_id = config.getint("telegram", "api_id")

        session_path = config.get("general", "session")
        spawner = TGSpawner(tg_api_hash=api_hash, tg_api_id=api_id, path=session_path, logger=logger)

        if login:
            login_kwargs = {}
            if bot_login_uses_token:
                login_kwargs["bot_token"] = config.get("telegram", "token", fallback=None)
            await spawner.login(**login_kwargs)
            return

        if not os.path.exists(session_path):
            print(f"Session file '{session_path}' is missing!")
            sys.exit(1)

        try:
            context["client"] = await spawner.load_account()
        except BadAccountError:
            print(f"Session '{session_path}' is expired or invalid. Re-run with --login to authenticate.")
            sys.exit(1)

        if needs_user:
            user_session_path = config.get("general", "user_session")
            user_spawner = TGSpawner(tg_api_hash=api_hash, tg_api_id=api_id, path=user_session_path, logger=logger)

            if user_login:
                await user_spawner.login()
                return

            if not os.path.exists(user_session_path):
                print(f"Session file '{user_session_path}' is missing!")
                sys.exit(1)

            try:
                context["user"] = await user_spawner.load_account()
            except BadAccountError:
                print(f"Session '{user_session_path}' is expired or invalid. Re-run with --user-login to authenticate.")
                sys.exit(1)

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
