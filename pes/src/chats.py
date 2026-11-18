from configparser import ConfigParser


def chat_ids(config: ConfigParser, section: str, option: str) -> list[int]:
    """A comma- or space-separated list of chat IDs; empty when unset. A list, since Telethon's `chats=` filter
    reads a frozenset as one chat."""
    return [int(c) for c in config.get(section, option, fallback="").replace(",", " ").split()]
