import logging
import sys
import os
logger = None

if not logger:
    logging.debug("Generating logger")
    logFormatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger()
    logger.handlers.clear()
    consoleHandler = logging.StreamHandler(sys.stderr)
    consoleHandler.setFormatter(logFormatter)
    logger.addHandler(consoleHandler)
    logger.setLevel(os.getenv("LOG_LEVEL", "WARNING").upper())