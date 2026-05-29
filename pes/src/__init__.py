from kbots_common import main_cli as common_main_cli
from kbots_common import run_bot

from .tmodules import init as tinit


def main(cpath: str, login=False, user_login=False):
    return run_bot(
        cpath,
        module_init=tinit,
        logger_name="vtraty-pes-bot",
        login=login,
        user_login=user_login,
        needs_user=True,
        bot_login_uses_token=True,
    )


def main_cli():
    return common_main_cli(
        module_init=tinit,
        logger_name="vtraty-pes-bot",
        needs_user=True,
        bot_login_uses_token=True,
    )
