from bot import TraderBot

import threading
import time
import sys
import os.path
import logging
import json
from math import isnan, floor, ceil
from functools import reduce

from pyti.stochrsi import stochrsi
from pyti.simple_moving_average import simple_moving_average as sma


TWO_HOURS = 7200000
THIRTY_MINUTES = TWO_HOURS / 4.0

class VolumeBot(TraderBot):
  def load_products(self):
    all_products = self.client.get_products()['data']
    #return list(filter(lambda item: item['symbol'] == 'BTCUSDT' or item['symbol'].endswith('BTC'), all_products))
    products_filtered = list(filter(lambda item: item['symbol'].endswith('BTC'), all_products))

    for product in products_filtered:
      symbol = product['symbol']
      logging.info("Loading historical data for {}...".format(symbol))
      self._klines[symbol] = self.client.get_historical_klines(symbol=symbol, interval='5m', start_str='12 hours ago UTC')
      self._klines2[symbol] = self.client.get_historical_klines(symbol=symbol, interval='1h', start_str='3 days ago UTC')
      self._start_size = len(self._klines[symbol])

    return products_filtered

  def balances_loop(self):
    timeout = 1.0

    while self._running:
      self._balances = self.get_balance()

      for ticker_symbol in self._balances:
        if ticker_symbol == 'BTC' or ticker_symbol == 'BNB' or ticker_symbol == 'USDT':
          continue

        coin_price = float(self._klines[ticker_symbol][-1][4])

        if coin_price * float(self._balances[ticker_symbol]) <= 0.0005:
          # coin is dust; skip
          continue

        print("BALANCE CHECKER: Check {}".format(coin))

      time.sleep(timeout)

  def loop(self):
    timeout = 3 / float(len(self.products))
    counter = 0

    by_volume = [] # array of dicts

    # TODO: for coins that are in the balance, sell once order book is tending towards selling side
    # maybe also last 10 trades could be checked and if most are sell, then sell as well.
    
    # TODO: move checker for coins in balance into a separate, faster loop.

    while self._running:
      self._balances = self.get_balance() # refresh balance

      product = self.products[counter]
      ticker_symbol = product['symbol']

      if ticker_symbol.endswith('BTC'):
        idx = ticker_symbol.index('BTC')
      elif ticker_symbol.endswith('USDT'):
        idx = ticker_symbol.index('USDT')

      coin1 = ticker_symbol[:idx]
      coin2 = ticker_symbol[idx:]

      if self.is_coin_blacklisted(coin1):
        logging.info("{}: blacklisted, skipping.".format(coin1))
      else:

        #logging.info("Check {}...".format(ticker_symbol))

        try:
          # get klines since close
          new_klines = self.client.get_klines(symbol=ticker_symbol, interval='5m', startTime=self._klines[ticker_symbol][-1][0])
          new_klines2 = self.client.get_klines(symbol=ticker_symbol, interval='1h', startTime=self._klines2[ticker_symbol][-1][0])

          for kline_group in [{ 'new': new_klines, 'old': self._klines }, { 'new': new_klines2, 'old': self._klines2 }]:
            for kline in kline_group['new']:
              if kline[0] != kline_group['old'][ticker_symbol][-1][0]:
                kline_group['old'][ticker_symbol].append(kline)
              else:
                kline_group['old'][ticker_symbol][-1] = kline

            if len(kline_group['old'][ticker_symbol]) > self._start_size:
              kline_group['old'][ticker_symbol].pop(0)

          # short term
          volumes = list(map(lambda item: float(item[5]), self._klines[ticker_symbol][-30:]))
          volumes_sorted = sorted(volumes)

          median_volume = 0
          mean_volume = reduce(lambda x, y: x + y, volumes_sorted) / len(volumes_sorted)

          if len(volumes_sorted) % 2 != 0:
            mid = len(volumes_sorted) / 2
            f = floor(mid)
            c = ceil(mid)

            assert f < c, "floor should be less than ceiling ({} vs {})".format(f, c)

            median_volume = (volumes_sorted[f] + volumes_sorted[c]) / 2
          else:
            median_volume = volumes_sorted[len(volumes_sorted) // 2]

          # divide median by mean
          med_mean = median_volume / mean_volume
          last_volume = volumes[-1]
          last_volume_mean = last_volume / mean_volume

          ticker_data = self.client.get_ticker(symbol=ticker_symbol)
          volume_btc = float(ticker_data['volume']) * float(ticker_data['lastPrice'])

          if float(self._klines[ticker_symbol][-2][1]) <= float(self._klines[ticker_symbol][-2][4]):
            if (coin1 not in self._balances or self._balances[coin1] == 0) and (coin2 in self._balances and self._balances[coin2] > 0):
              price_closes = list(map(lambda item: float(item[4]), self._klines[ticker_symbol]))
              sma7 = sma(price_closes, 7)
              sma20 = sma(price_closes, 20)

              logging.info("{}: SMA7: {}, SMA20: {}".format(ticker_symbol, sma7[-1], sma20[-1]))

              depth = self.client.get_order_book(symbol=ticker_symbol, limit=10)
              bid_sum = reduce(lambda x, y: x + y, list(map(lambda item: float(item[1]), depth['bids'])))
              ask_sum = reduce(lambda x, y: x + y, list(map(lambda item: float(item[1]), depth['asks'])))
              logging.info("{} bid sum: {}, ask sum: {}".format(ticker_symbol, bid_sum, ask_sum))

              if (volume_btc > 200) and (last_volume_mean / med_mean > 2) and (price_closes[-1] > sma7[-1]) and (sma7[-1] > sma20[-1]):
                price_closes_lt = list(map(lambda kline: float(kline[4]), self._klines2[ticker_symbol]))
                stoch_rsi_results = stochrsi(price_closes_lt, 14)

                logging.info("{} SRSI : {}".format(ticker_symbol, stoch_rsi_results[-1]))

                if stoch_rsi_results[-1] >= 60:
                  logging.info("skip {}, already pumped".format(ticker_symbol))
                elif bid_sum < ask_sum:
                  logging.info("Not buying {} because bid sum ({}) < ask sum ({})".format(ticker_symbol, bid_sum, ask_sum))
                else:
                  self.buy(symbol=ticker_symbol, coin1=coin1, coin2=coin2)
                  self.blacklist_coin(best_coin['coin1'], THIRTY_MINUTES)

                # by_volume.append({
                #   'symbol': ticker_symbol,
                #   'coin1': coin1,
                #   'coin2': coin2,
                #   'value': last_volume_mean / med_mean,
                #   'sma7': sma7
                # })

          # if last_volume_mean / med_mean > 2:
          #   logging.info("\n\n!! {} Abnormal volume = {} / {}\n\n".format(ticker_symbol, last_volume_mean, med_mean))

          #   if float(self._klines[ticker_symbol][-2][1]) > float(self._klines[ticker_symbol][-2][4]):
          #     logging.info("{} is likely falling (red candle), not buying.".format(ticker_symbol))
          #   else:
          #     self.buy(ticker_symbol, coin1, coin2)

          coin_amount_held = 0
          coin_is_dust = True
          trailing_stop_triggered = False

          if coin1 in self._balances:
            coin_amount_held = self._balances[coin1]
            coin_price = float(self._klines[ticker_symbol][-1][4])

            if coin2 == 'BTC':
              if coin_price * coin_amount_held >= 0.0005: # lt ~$5 worth of bitcoin
                coin_is_dust = False
            elif coin2 == 'USDT':
              if coin_price * coin_amount_held >= 5: # lt ~$5
                coin_is_dust = False
            else:
              raise Exception("Unsure how to handle coin2 type: {}".format(coin2))

          if coin_is_dust:
            self._balances[coin1] = 0 # just set to zero
          else:
            if ticker_symbol in self._trailing_stops:
              if coin_price <= self._trailing_stops[ticker_symbol]:
                logging.info("{}: Trailing stop triggered @ {}".format(ticker_symbol, coin_price))
                self.sell(ticker_symbol, coin1, coin2)
                self.blacklist_coin(coin1, TWO_HOURS)
                trailing_stop_triggered = True
              else:
                self.update_trailing_stop_for(ticker_symbol)

          
        except Exception as ex:
          logging.error("Error: {}".format(ex))

      counter += 1
      counter %= len(self.products)

      if False and counter == 0:
        by_volume_sorted = sorted(by_volume, key=lambda item: item['value'])

        best_coin = None

        for i in range(len(by_volume_sorted) - 1, -1, -1):
          item = by_volume_sorted[i]

          last_sma7 = item['sma7'][-1]
          # find a recent candle with most volume, determine red or green
          last_klines = self._klines[item['symbol']][-4:]
          last_kline = self._klines[item['symbol']][-1]

          

          top_kline = None
          for kline in last_klines:
            if top_kline is None or (float(kline[5]) > float(top_kline[5])):
              top_kline = kline

          assert top_kline is not None
          logging.info("{} top_kline = {}".format(item['symbol'], top_kline))

          # determine red or green
          if float(top_kline[1]) > float(top_kline[4]) or float(last_kline[1]) > float(last_kline[4]):
            logging.info("skip {} because dump determined ({} > {})".format(item['symbol'], top_kline[1], top_kline[4]))
          else:

            price_closes = list(map(lambda kline: float(kline[4]), self._klines[item['symbol']]))
            stoch_rsi_results = stochrsi(price_closes, 14)

            logging.info("{} SRSI : {}".format(item['symbol'], stoch_rsi_results[-1]))

            if stoch_rsi_results[-1] >= 60:
              logging.info("skip {}, already pumped".format(item['symbol']))
            else:
              if float(last_klines[-2][4]) > last_sma7:
                best_coin = item
                break
              else:
                logging.info("{} is below sma ({}, {})".format(item['symbol'], last_sma7, last_klines[-2][4]))

        assert best_coin is not None

        logging.info("by_volume_sorted = {}".format(by_volume_sorted))
        #logging.info("\nbest_coin = {}\n".format(best_coin))
        self.buy(symbol=best_coin['symbol'], coin1=best_coin['coin1'], coin2=best_coin['coin2'])
        self.blacklist_coin(best_coin['coin1'], THIRTY_MINUTES)

        by_volume = []

      time.sleep(timeout)


from bootstrap import TradeBotBootstrap
TradeBotBootstrap(VolumeBot).start_main_loop()
