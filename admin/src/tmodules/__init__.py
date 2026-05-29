from kbots_common.tmodules import init_modules


async def init(**context):
    await init_modules(__name__, __file__, **context)
