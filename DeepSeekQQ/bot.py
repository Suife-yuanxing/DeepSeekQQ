import nonebot
from nonebot.adapters.onebot.v11 import Adapter

nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(Adapter)
nonebot.load_plugins("plugins")

if __name__ == "__main__":
    try:
        nonebot.run()
    except Exception as e:
        import logging
        logging.getLogger("deepseek").critical(f"FATAL: {type(e).__name__}: {e}", exc_info=True)
        raise
