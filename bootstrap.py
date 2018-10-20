import logging
import time

LOG_FORMAT = '%(asctime)-15s %(message)s'
logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT)
fh = logging.FileHandler('log.log')
logging.getLogger("").addHandler(fh)

class TradeBotBootstrap:
  def __init__(self, trader_bot_class):
    self.trader_bot_class = trader_bot_class
    self.bot = None
    self.num_retries = 0

  def start_main_loop(self):
    self.bot = self.trader_bot_class()
    self.bot.start()

    while self.bot._running:
      self.num_retries = 0 # clear out previous error retries

      try:
        time.sleep(0.1)
      except KeyboardInterrupt:
        logging.info("Quitting.")
        self.bot._running = False
      except Exception as ex:
        logging.error("ERROR: {}".format(ex))
        self.bot._running = False
        return self.retry()

  def retry(self):
    if self.num_retries < 3:
      self.num_retries += 1

      timer = 0

      while timer < 30:
        logging.warning("[Re-initialization attempt {}/3]: retrying in {}s...".format(self.num_retries, 30 - timer))
        timer += 1
        time.sleep(1)

      self.bot._running = False
      return self.start_main_loop()
    else:
      # wait 15 mins since retries failed previously
      logging.warning("Re-initialization failed after three attempts, trying again in 15 minutes.")
      self.num_retries = 0
      
      timer = 0

      while timer < 15:
        logging.warning("Attempting to retry in in {}s...".format((15 * 60) - timer))

        timer += 0.5
        time.sleep(30)

      return self.retry()