import importlib
import os


async def init_modules(package_name: str, package_file: str, **context):
    context["logger"].debug("Loading and initializing telegram modules ...")

    modules = [
        importlib.import_module(".", f"{package_name}.{file[:-3]}")
        for file in os.listdir(os.path.dirname(package_file))
        if file[0].isalpha() and file.endswith(".py")
    ]
    await start_modules(context, modules)


async def start_modules(context, modules):
    for module in modules:
        context["logger"].debug("Loading telegram module: '%s' ...", module.__name__)
        p_init = getattr(module, "init", None)
        if callable(p_init):
            try:
                await p_init(**context)
            except Exception:
                context["logger"].exception("Failed to load '%s'!", module.__name__)
