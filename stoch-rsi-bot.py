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

class StochRsiBot(TraderBot):
  def load_products(self):
    all_products = self.client.get_products()['data']
    #return list(filter(lambda item: item['symbol'] == 'BTCUSDT' or item['symbol'].endswith('BTC'), all_products))
    products_filtered = list(filter(lambda item: item['symbol'].endswith('BTC'), all_products))

    for product in products_filtered:
      symbol = product['symbol']
      logging.info("Loading historical data for {}...".format(symbol))
      self._klines[symbol] = self.client.get_historical_klines(symbol=symbol, interval='15m', start_str='3 days ago UTC')
      self._klines2[symbol] = self.client.get_historical_klines(symbol=symbol, interval='1h', start_str='3 days ago UTC')
      self._start_size = len(self._klines[symbol])

    return products_filtered

  def loop(self):
    timeout = self.get_timeout()
    counter = 0

    oversold_ary = [] # array of dicts

    while self._running:
      self._balances = self.get_balance() # refresh balance

      product = self.products[counter]
      ticker_symbol = product['symbol']

      logging.info("Check {}...".format(ticker_symbol))

      try:

        # get klines since close
        new_klines = self.client.get_klines(symbol=ticker_symbol, interval='15m', startTime=self._klines[ticker_symbol][-1][0])
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
        price_closes = list(map(lambda item: float(item[4]), self._klines[ticker_symbol][:-1]))
        stoch_rsi_results = stochrsi(price_closes, 14)

        # long term
        price_closes_lt = list(map(lambda item: float(item[4]), self._klines2[ticker_symbol]))
        stoch_rsi_results_lt = stochrsi(price_closes_lt, 14)

        if ticker_symbol.endswith('BTC'):
          idx = ticker_symbol.index('BTC')
        elif ticker_symbol.endswith('USDT'):
          idx = ticker_symbol.index('USDT')

        coin1 = ticker_symbol[:idx]
        coin2 = ticker_symbol[idx:]

        ticker_data = self.client.get_ticker(symbol=ticker_symbol)

        volume_btc = float(ticker_data['volume']) * float(ticker_data['lastPrice'])

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

        if not trailing_stop_triggered:
          if stoch_rsi_results[-1] >= 60:
            if coin1 in self._balances and self._balances[coin1]:
              if coin1 != 'BNB': # don't sell BNB
                if coin1 != 'BTC' or self._balances['USDT'] == 0: # don't trade all btc at once, so we can keep trading alts
                  logging.info("{}: overbought ({}) @ {}".format(coin1, stoch_rsi_results[-1], self._klines[ticker_symbol][-1][4]))
                  self.sell(ticker_symbol, coin1, coin2)
          elif stoch_rsi_results[-1] <= 20:
            if volume_btc >= 100:
              oversold_ary.append({
                'symbol': ticker_symbol,
                'volume': volume_btc,
                'coin1': coin1,
                'coin2': coin2,
                'srsi': stoch_rsi_results[-1],
                'srsi2': stoch_rsi_results_lt[-1]
              })

              logging.info("{}: oversold ({}) @ {} :: (rsi: {}, long term: {})".format(coin1, stoch_rsi_results[-1], self._klines[ticker_symbol][-1][4], stoch_rsi_results[-1], stoch_rsi_results_lt[-1]))
              
              #if (coin1 not in self._balances or self._balances[coin1] == 0) and (coin2 in self._balances and self._balances[coin2] > 0):
              #  print("{} oversold ({}) @ {}".format(coin1, stoch_rsi_results[-1], self._klines[symbol][-1][4]))
              #  self.buy(symbol, coin1, coin2)
            else:
              logging.info("Volume not high enough for {} ({} BTC), skipping...".format(ticker_symbol, volume_btc))
      except Exception as ex:
        logging.error("Error: {}".format(ex))

      counter += 1
      counter %= len(self.products)

      if counter == 0:
        # sort oversold by lowest rsi
        # oversold_by_score = [[] * 10]
        # for item in oversold_ary:
        #   score = round(item['srsi'] / 10)
        #   oversold_by_score[score].append(item)


        # for sub in oversold_by_score:

        oversold_sorted = sorted(oversold_ary, key=lambda item: item['srsi'])
        oversold_final = []

        for item in oversold_sorted:
          coin1 = item['coin1']
          coin2 = item['coin2']
          ticker_symbol = item['symbol']
          srsi = item['srsi']
          srsi2 = item['srsi2']

          # check if the coin has possibly been dumped by calculating the median volume
          volumes = map(lambda kline: float(kline[5]), self._klines[item['symbol']])  #sorted(key=lambda item: float(item[5]))
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

          if med_mean < 0.7:
            logging.info("{} has likely been dumped, skipping...".format(coin1))
          else:
            
            # prevent from buying already pumped coins
            #if stoch_rsi_results_lt[-1] != 100:
            #  print("{} has likely already been pumped, not buying.".format(coin1))
            #else:
            if (coin1 not in self._balances or self._balances[coin1] == 0) and (coin2 in self._balances and self._balances[coin2] > 0):
              if self.is_coin_blacklisted(coin1):
                logging.info("{}: blacklisted, skipping.".format(coin1))
              else:
                oversold_final.append(item)
                #self.buy(ticker_symbol, coin1, coin2)

        logging.info("FINAL: {}".format(oversold_final))

        for item in oversold_final:
          coin1 = item['coin1']
          coin2 = item['coin2']
          ticker_symbol = item['symbol']
          srsi = item['srsi']
          srsi2 = item['srsi2']

          self.buy(ticker_symbol, coin1, coin2)

        oversold_ary = []

      time.sleep(timeout)


from bootstrap import TradeBotBootstrap
TradeBotBootstrap(StochRsiBot).start_main_loop()


# TODO: for each pair: every minute, get 15 minute candles and calculate stoch rsi.