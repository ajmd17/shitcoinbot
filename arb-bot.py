from bot import TraderBot

import threading
import time
import sys
import os.path
import logging
import json
from math import isnan, floor, ceil
from functools import reduce
from decimal import *

class ArbitrageBot(TraderBot):
  def __init__(self):
    self.coin_groups = {}
    super().__init__()

  def load_products(self):
    all_products = self.client.get_products()['data']
    products = []

    for product in all_products:
      symbol = product['symbol']

      if symbol.endswith('BTC'):
        index = symbol.index('BTC')
        coin_name = symbol[:index]

        assert coin_name not in self.coin_groups, "{} should not be in self.coin_groups yet because it is a BTC pair.".format(coin_name)

        other_products = list(filter(lambda item: coin_name == item['baseAsset'] and (not item['symbol'].endswith('BTC')), all_products))
        self.coin_groups[coin_name] = [symbol] + list(map(lambda item: item['symbol'], other_products))

        products.append(product)

    logging.info("self.coin_groups = {}".format(self.coin_groups))

    # for product in products_filtered:
    #   symbol = product['symbol']
    #   logging.info("Loading historical data for {}...".format(symbol))
    #   self._klines[symbol] = self.clientget_historical_klines(symbol=symbol, interval='15m', start_str='3 days ago UTC')
    #   self._klines2[symbol] = self.clientget_historical_klines(symbol=symbol, interval='1h', start_str='3 days ago UTC')
    #   self._start_size = len(self._klines[symbol])

    return products

  def loop(self):
    timeout = 60.0 / float(len(self.coin_groups.items()))
    counter = 0

    oversold_ary = [] # array of dicts

    while self._running:
      self._balances = self.get_balance() # refresh balance

      product = self.products[counter]
      ticker_symbol = product['symbol']

      if ticker_symbol.endswith('BTC'):
        idx = ticker_symbol.index('BTC')
        coin1 = ticker_symbol[:idx]
        #logging.info("Check {}...".format(coin1))
        assert coin1 in self.coin_groups, "{} not in coin_groups".format(coin1)

        coin_group = self.coin_groups[coin1]

        if len(coin_group) > 1:
          #logging.info("\t{}".format(coin_group))

          # get prices for coin group...
          prices_btc = [] # store the converted prices in BTC.

          for pair in coin_group:
            assert pair.startswith(coin1), "{} should start with {}".format(pair, coin1)

            coin2 = pair[len(coin1):]
            #logging.info("\tCheck {} / {}...".format(coin1, coin2))

            # get price of item
            data = self.client.get_ticker(symbol=pair)

            #logging.info("\tData = {}".format(data))

            if coin2 == 'BTC':
              prices_btc.append({
                'pair': pair,
                'coin1': coin1,
                'coin2': coin2,
                'bid': Decimal(data['bidPrice']),
                'bidQty': Decimal(data['bidQty']),
                'ask': Decimal(data['askPrice']),
                'askQty': Decimal(data['askQty'])
              })
            elif coin2 == 'USDT':
              coin2_data = self.client.get_ticker(symbol="BTC{}".format(coin2))
              prices_btc.append({
                'pair': pair,
                'coin1': coin1,
                'coin2': coin2,
                'bid': Decimal(data['bidPrice']) / Decimal(coin2_data['askPrice']),
                'bidQty': Decimal(data['bidQty']),
                'ask': Decimal(data['askPrice']) / Decimal(coin2_data['bidPrice']),
                'askQty': Decimal(data['askQty'])
              })
            else:
              # look up price of second coin in BTC.
              coin2_data = self.client.get_ticker(symbol="{}BTC".format(coin2))
              prices_btc.append({
                'pair': pair,
                'coin1': coin1,
                'coin2': coin2,
                'bid': Decimal(data['bidPrice']) * Decimal(coin2_data['askPrice']),
                'bidQty': Decimal(data['bidQty']),
                'ask': Decimal(data['askPrice']) * Decimal(coin2_data['bidPrice']),
                'askQty': Decimal(data['askQty'])
              })
          
          #logging.info("prices_btc = {}".format(prices_btc))

          # find best arb opp here
          highest_bid = None
          lowest_ask = None

          for item in prices_btc:
            pair = item['pair']
            bid = item['bid']
            ask = item['ask']

            if highest_bid is None or bid > highest_bid['bid']:
              highest_bid = item

            if lowest_ask is None or ask < lowest_ask['ask']:
              lowest_ask = item

          percentage_diff = Decimal(1.0) - (lowest_ask['ask'] / highest_bid['bid'])
          percentage_diff_fees = percentage_diff - (Decimal(0.0005) * Decimal(2))
          #logging.info("\n{}: {}% profitability ({}% with fees included) \tHIGHEST BID: ({}, {})\tLOWEST ASK: ({}, {})\n".format(coin1, percentage_diff * Decimal(100.0), percentage_diff_fees * Decimal(100.0), highest_bid['pair'], highest_bid['value'], lowest_ask['pair'], lowest_ask['value']))

          if percentage_diff_fees > Decimal(0.0): #lowest_ask['value'] < highest_bid['value']:]
            logging.info("\n\nARB OPP FOUND IN {} vs. {} ({}% profitability) (askQty: {})".format(lowest_ask['pair'], highest_bid['pair'], percentage_diff_fees * Decimal(100.0), lowest_ask['askQty']))
            #logging.info("Max amt to buy: {}".format(self._balances['BTC']))
            max_amt = min(Decimal(self._balances[lowest_ask['coin2']]), Decimal(0.002))
            # for now just use btc
            if lowest_ask['coin2'] == 'BTC':
              qty = min(max_amt / lowest_ask['ask'], lowest_ask['askQty'])
              logging.info("qty = {}".format(qty))
              #logging.info("Max amt to buy ({}): {}".format(lowest_ask['coin2'], max_amt))
              #exit()

              self.buy(symbol=lowest_ask['pair'], coin1=lowest_ask['coin1'], coin2=lowest_ask['coin2'], coin_price=float(lowest_ask['ask']), qty=float(qty))
              time.sleep(5)
              self.sell(symbol=highest_bid['pair'], coin1=highest_bid['coin1'], coin2=highest_bid['coin2'], coin_price=float(highest_bid['bid']), qty=float(qty))

              exit()

      counter += 1
      counter %= len(self.products)

      time.sleep(timeout)


from bootstrap import TradeBotBootstrap
TradeBotBootstrap(ArbitrageBot).start_main_loop()