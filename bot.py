from binance.client import Client
client = Client("PUT YOUR KEYS HERE!", "THE OTHER KEY GOES HERE!", { 'timeout': 20 })

import threading
import time
import sys
import os.path
import logging
import json
from math import isnan, floor, ceil
from functools import reduce

FEE = 0.0005
AMOUNT_TO_BUY = 0.0025
STOPLOSS = 0.98
PAPER_TRADER = False

TWO_HOURS = 7200000

class TraderBot:
  def __init__(self):
    self.client = client
    self._klines = {}
    self._klines2 = {} # klines for longer timespan
    self.products = self.load_products()
    self._running = False
    #self._balances = {
    #  'BTC': 1,
    #  'USDT': 0
    #}
    self._balances = self.get_balance()
    self._exchange_info = client.get_exchange_info()

    self._trailing_stops = {}
    self.load_trailing_stops()

    self._blacklist = {}
    self.load_blacklist()

  def is_coin_blacklisted(self, coin):
    timestamp = int(time.time())

    if coin in self._blacklist:
      assert "expires" in self._blacklist[coin]

      expiry_dt = int(self._blacklist[coin]['expires'])

      if expiry_dt == 0:
        return True

      if timestamp >= expiry_dt:
        del self._blacklist[coin]
        self.save_blacklist()

        return False

    return False

  def load_blacklist(self):
    if os.path.isfile('blacklist.json'):
      self._blacklist = json.load(open('blacklist.json'))
      logging.info("blacklist: ", self._blacklist)

  def save_blacklist(self):
    with open('blacklist.json', 'w') as f:
      json.dump(self._blacklist, f)

  def blacklist_coin(self, coin, duration=None):
    if duration == None or duration == 0:
      self._blacklist[coin] = {
        'expires': 0
      }
    else:
      self._blacklist[coin] = {
        'expires': int(time.time()) + duration
      }

    logging.info("{}: blacklist (expires: {})".format(coin, self._blacklist[coin]['expires']))

    self.save_blacklist()

  def load_trailing_stops(self):
    if os.path.isfile('stops.json'):
      self._trailing_stops = json.load(open('stops.json'))
      logging.info("stops: ", self._trailing_stops)

  def save_trailing_stops(self):
    with open('stops.json', 'w') as f:
      json.dump(self._trailing_stops, f)
  
  def get_balance(self):
    result = {}
    account = client.get_account(recvWindow=6000000)

    for item in account['balances']:
      result[item['asset']] = float(item['free'])

    return result

  def update_trailing_stop_for(self, key):
    if key in self._klines:
      new_trailing_stop = float(self._klines[key][-1][4]) * 0.97

      if key in self._trailing_stops:
        current_trailing_stop = self._trailing_stops[key]
        self._trailing_stops[key] = max(current_trailing_stop, new_trailing_stop)

        if self._trailing_stops[key] != current_trailing_stop:
          logging.info("{}: set trailing stop from {} to {}".format(key, current_trailing_stop, new_trailing_stop))
      else:
        self._trailing_stops[key] = new_trailing_stop
        logging.info("{}: set trailing stop to {}".format(key, new_trailing_stop))
    else:
      logging.warning("{}: price not yet loaded".format(key))

    self.save_trailing_stops()

    return self._trailing_stops

  def load_products(self):
    raise NotImplementedError()

  def start(self):
    self._loop_thread = threading.Thread(target=self.loop)
    self._balances_loop_thread = threading.Thread(target=self.balances_loop)
    #self._loop_thread.daemon = True
    self._running = True
    self._loop_thread.start()
    self._balances_loop_thread.start()

  def loop(self):
    raise NotImplementedError()

  def balances_loop(self):
    pass

  def get_timeout(self):
    return 20.0 / float(len(self.products))

  def buy(self, symbol, coin1, coin2, coin_price=None, qty=None, trailing_stop=True):
    if coin_price is None:
      logging.info("BUY {} @ {}".format(coin1, self._klines[symbol][-1][4]))

      coin_price = float(self._klines[symbol][-1][4])

    if trailing_stop:
      self.update_trailing_stop_for(symbol)

    if PAPER_TRADER:
      if coin1 not in self._balances:
        self._balances[coin1] = 0

      if coin2 == 'USDT':
        self._balances[coin1] += (25 * coin_price) - ((25 * coin_price) * FEE)
        self._balances[coin2] -= 25
      elif coin2 == 'BTC':
        self._balances[coin1] += (AMOUNT_TO_BUY / coin_price) - ((AMOUNT_TO_BUY / coin_price) * FEE)
        self._balances[coin2] -= AMOUNT_TO_BUY
    else:
      if coin2 == 'USDT':
        client.order_market_buy(symbol=symbol, quantity=25*coin_price)
      elif coin2 == 'BTC':
        if qty is None:
          qty = self.get_quantity(symbol, coin_price)
        else:
          step_size = self.get_step_size(symbol)
          qty = floor((qty / coin_price) / step_size) * step_size

        assert qty != 0, "qty should not be zero"

        try:
          logging.info('buy {} ({})'.format(symbol, qty))
          client.order_market_buy(symbol=symbol, quantity=qty)
        except Exception as ex:
          logging.error("Failed to buy {}: {}".format(symbol, ex))

    if not PAPER_TRADER:
      self._balances = self.get_balance()
    #self.print_total_balance()

  def sell(self, symbol, coin1, coin2, coin_price=None, qty=None):
    if coin_price is None:
      logging.info("SELL {} @ {}".format(coin1, self._klines[symbol][-1][4]))

      coin_price = float(self._klines[symbol][-1][4])

    if symbol in self._trailing_stops:
      del self._trailing_stops[symbol]

    if PAPER_TRADER:
      if coin1 == 'BTC':
        self._balances[coin2] += (self._balances[coin1] * coin_price / 2) - ((self._balances[coin1] * coin_price / 2) * FEE)
        self._balances[coin1] = self._balances[coin1] / 2
      else:
        self._balances[coin2] += (self._balances[coin1] * coin_price) - (self._balances[coin1] * coin_price * FEE)
        self._balances[coin1] = 0
    else:
      if coin1 == 'BTC':
        client.order_market_sell(symbol=symbol, quantity=self._balances[coin1])
      else:
        step_size = self.get_step_size(symbol)
        if qty is None:
          qty = self._balances[coin1]
        qty = floor(qty / step_size) * step_size
        assert qty != 0, "qty should not be zero"
        client.order_market_sell(symbol=symbol, quantity=qty)

    if not PAPER_TRADER:
      self._balances = self.get_balance()
    #self.print_total_balance()

  def getprice(self, symbol):
    return float(self._klines[symbol][-1][4])

  def get_step_size(self, symbol):
    symbol_info = self.get_symbol_info(symbol)
    assert symbol_info is not None

    filters = symbol_info['filters']
    step_size = None

    for f in filters:
      if f['filterType'] == 'LOT_SIZE':
        step_size = float(f['stepSize'])

    assert step_size is not None

    return step_size

  def get_quantity(self, symbol, coin_price):
    step_size = self.get_step_size(symbol)

    qty = floor((AMOUNT_TO_BUY / coin_price) / step_size) * step_size
    return qty

  def get_symbol_info(self, symbol):
    symbols = self._exchange_info['symbols']

    for item in symbols:
      if item['symbol'] == symbol:
        return item
    
    return None

  def print_total_balance(self):
    if not PAPER_TRADER:
      self._balances = self.get_balance()

    logging.info("balances: {}".format(self._balances))

    # calculate total worth
    total_worth_btc = 0

    for key, value in self._balances.items():
      if value != 0:
        if key == 'BTC':
          total_worth_btc += value
        elif key == 'USDT':
          if 'BTCUSDT' in self._klines:
            total_worth_btc += value / self.getprice('BTCUSDT')
        else:
          total_worth_btc += value * self.getprice(key + 'BTC')

