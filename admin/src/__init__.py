from kbots_common import main_cli as common_main_cli
from kbots_common import run_bot

from .tmodules import init as tinit


def main(cpath: str, login=False):
    return run_bot(
        cpath,
        module_init=tinit,
        logger_name="vtraty-admin-bot",
        login=login,
    )


def main_cli():
    return common_main_cli(module_init=tinit, logger_name="vtraty-admin-bot")
