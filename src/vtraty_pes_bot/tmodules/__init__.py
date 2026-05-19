import importlib
import os


async def init(**context):
    context["logger"].debug("Loading and initializing telegram modules ...")

    await start_modules(
        context,
        modules=[
            # Dynamically import
            importlib.import_module(".", f"{__name__}.{file[:-3]}")
            # All the files in the current directory
            for file in os.listdir(os.path.dirname(__file__))
            # If they start with a letter and are Python files
            if file[0].isalpha() and file.endswith(".py")
        ],
    )


async def start_modules(context, modules):
    for module in modules:
        context["logger"].debug("Loading telegram module: '%s' ...", module.__name__)
        p_init = getattr(module, "init", None)
        if callable(p_init):
            try:
                await p_init(**context)
            except Exception:
                context["logger"].exception("Failed to load '%s'!", module.__name__)
