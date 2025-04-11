from datamodel import OrderDepth, TradingState, Order, Symbol, Listing, Trade, Observation, ProsperityEncoder
from typing import Dict, List, Any
import collections
import copy
import json

class Logger:
    def __init__(self) -> None:
        self.logs = ""
        self.max_log_length = 3750

    def print(self, *objects: Any, sep: str = " ", end: str = "\n") -> None:
        self.logs += sep.join(map(str, objects)) + end

    def flush(self, state: TradingState, orders: dict[Symbol, list[Order]], conversions: int, trader_data: str) -> None:
        base_length = len(
            self.to_json(
                [
                    self.compress_state(state, ""),
                    self.compress_orders(orders),
                    conversions,
                    "",
                    "",
                ]
            )
        )

        # We truncate state.traderData, trader_data, and self.logs to the same max. length to fit the log limit
        max_item_length = (self.max_log_length - base_length) // 3

        print(
            self.to_json(
                [
                    self.compress_state(state, self.truncate(state.traderData, max_item_length)),
                    self.compress_orders(orders),
                    conversions,
                    self.truncate(trader_data, max_item_length),
                    self.truncate(self.logs, max_item_length),
                ]
            )
        )

        self.logs = ""

    def compress_state(self, state: TradingState, trader_data: str) -> list[Any]:
        return [
            state.timestamp,
            trader_data,
            self.compress_listings(state.listings),
            self.compress_order_depths(state.order_depths),
            self.compress_trades(state.own_trades),
            self.compress_trades(state.market_trades),
            state.position,
            self.compress_observations(state.observations),
        ]

    def compress_listings(self, listings: dict[Symbol, Listing]) -> list[list[Any]]:
        compressed = []
        for listing in listings.values():
            compressed.append([listing.symbol, listing.product, listing.denomination])

        return compressed

    def compress_order_depths(self, order_depths: dict[Symbol, OrderDepth]) -> dict[Symbol, list[Any]]:
        compressed = {}
        for symbol, order_depth in order_depths.items():
            compressed[symbol] = [order_depth.buy_orders, order_depth.sell_orders]

        return compressed

    def compress_trades(self, trades: dict[Symbol, list[Trade]]) -> list[list[Any]]:
        compressed = []
        for arr in trades.values():
            for trade in arr:
                compressed.append(
                    [
                        trade.symbol,
                        trade.price,
                        trade.quantity,
                        trade.buyer,
                        trade.seller,
                        trade.timestamp,
                    ]
                )

        return compressed

    def compress_observations(self, observations: Observation) -> list[Any]:
        conversion_observations = {}
        for product, observation in observations.conversionObservations.items():
            conversion_observations[product] = [
                observation.bidPrice,
                observation.askPrice,
                observation.transportFees,
                observation.exportTariff,
                observation.importTariff,
                observation.sugarPrice,
                observation.sunlightIndex,
            ]

        return [observations.plainValueObservations, conversion_observations]

    def compress_orders(self, orders: dict[Symbol, list[Order]]) -> list[list[Any]]:
        compressed = []
        for arr in orders.values():
            for order in arr:
                compressed.append([order.symbol, order.price, order.quantity])

        return compressed

    def to_json(self, value: Any) -> str:
        return json.dumps(value, cls=ProsperityEncoder, separators=(",", ":"))

    def truncate(self, value: str, max_length: int) -> str:
        if len(value) <= max_length:
            return value

        return value[: max_length - 3] + "..."


logger = Logger()



class Trader:
    def __init__(self):
        # Set the position limit for Resin
        self.position_limits = {
            "RAINFOREST_RESIN": 50
        }
        # Internal tracking of our current position (for convenience)
        self.position = {"RAINFOREST_RESIN": 0}
        # Resin is known to be anchored around 10,000
        self.resin_fair_price = 10000

    def values_extract(self, order_dict, buy=0):
        """
        Helper function modeled on the friend's code.
        For a given ordered dictionary of prices to volumes, this function calculates
        the cumulative volume and returns the best price (depending on the side).
        When buy == 0, we invert volumes (for sell side extraction).
        """
        tot_vol = 0
        best_val = -1
        mxvol = -1
        for price, vol in order_dict.items():
            if buy == 0:
                vol *= -1  # Invert sell volumes
            tot_vol += vol
            if tot_vol > mxvol:
                mxvol = tot_vol
                best_val = price
        return tot_vol, best_val

    def compute_orders_resin(self, product: str, order_depth: OrderDepth, acc_bid: float, acc_ask: float) -> List[Order]:
        """
        This method adapts a short-spread (market-making/market-taking) strategy
        for Resin based on our friend's pearls code.
        
        - The order book is scanned (using OrderedDict) to extract the best ask and bid prices.
        - Orders are placed only if the price deviates from our acceptable levels (acc_bid/acc_ask),
          which here are set equal to the fair price (10,000).
        - We undercut the current best bid (by +1) and best ask (by -1) to gain priority.
        - Position management is enforced to ensure we do not exceed our limit.
        """
        orders: List[Order] = []
        
        # Order book processing: create ordered dictionaries for sell (ask) and buy (bid) sides.
        osell = collections.OrderedDict(sorted(order_depth.sell_orders.items()))
        obuy = collections.OrderedDict(sorted(order_depth.buy_orders.items(), reverse=True))
        
        # Extract best sell and best buy prices via cumulative volumes
        sell_vol, best_sell_pr = self.values_extract(osell)
        buy_vol, best_buy_pr = self.values_extract(obuy, buy=1)
        
        # Current position on resin (cpos is local; initial position from our internal tracker)
        cpos = self.position.get(product, 0)
        
        mx_with_buy = -1
        # Process the ask side: if the ask is below acc_bid (or equals acc_bid when holding a negative position)
        # and we still have capacity to buy, then take the available volume.
        for ask, vol in osell.items():
            if ((ask < acc_bid) or ((self.position.get(product, 0) < 0) and (ask == acc_bid))) and cpos < self.position_limits[product]:
                mx_with_buy = max(mx_with_buy, ask)
                order_for = min(-vol, self.position_limits[product] - cpos)
                cpos += order_for
                assert(order_for >= 0)
                orders.append(Order(product, ask, order_for))
                
        # Compute our own market mid-price (for reference)
        mprice_actual = (best_sell_pr + best_buy_pr) / 2
        mprice_ours = (acc_bid + acc_ask) / 2
        
        # Define undercut prices based on best bid/ask from the order book
        undercut_buy = best_buy_pr + 1
        undercut_sell = best_sell_pr - 1
        
        # Set our own bid and ask prices by slightly shifting away from acc_bid and acc_ask
        bid_pr = min(undercut_buy, acc_bid - 1)
        sell_pr = max(undercut_sell, acc_ask + 1)
        
        # If we are in a negative position, try to buy more aggressively
        if (cpos < self.position_limits[product]) and (self.position.get(product, 0) < 0):
            num = min(40, self.position_limits[product] - cpos)
            orders.append(Order(product, min(undercut_buy + 1, acc_bid - 1), num))
            cpos += num
        
        # If we have a large long position (here arbitrarily if >15), reduce it by buying in an undercut fashion
        if (cpos < self.position_limits[product]) and (self.position.get(product, 0) > 15):
            num = min(40, self.position_limits[product] - cpos)
            orders.append(Order(product, min(undercut_buy - 1, acc_bid - 1), num))
            cpos += num
        
        # Place an additional buy order at our bid price if we still have capacity
        if cpos < self.position_limits[product]:
            num = min(40, self.position_limits[product] - cpos)
            orders.append(Order(product, bid_pr, num))
            cpos += num
        
        # Reset cpos to the actual current position from our state before selling
        cpos = self.position.get(product, 0)
        # Process the bid side: if the bid is above acc_ask (or equals acc_ask when holding a positive position)
        # and we have capacity to sell, then take the available volume.
        for bid, vol in obuy.items():
            if ((bid > acc_ask) or ((self.position.get(product, 0) > 0) and (bid == acc_ask))) and cpos > -self.position_limits[product]:
                order_for = max(-vol, -self.position_limits[product] - cpos)
                cpos += order_for
                assert(order_for <= 0)
                orders.append(Order(product, bid, order_for))
        
        # Additional sell orders to adjust our position:
        if (cpos > -self.position_limits[product]) and (self.position.get(product, 0) > 0):
            num = max(-40, -self.position_limits[product] - cpos)
            orders.append(Order(product, max(undercut_sell - 1, acc_ask + 1), num))
            cpos += num
        
        if (cpos > -self.position_limits[product]) and (self.position.get(product, 0) < -15):
            num = max(-40, -self.position_limits[product] - cpos)
            orders.append(Order(product, max(undercut_sell + 1, acc_ask + 1), num))
            cpos += num
        
        if cpos > -self.position_limits[product]:
            num = max(-40, -self.position_limits[product] - cpos)
            orders.append(Order(product, sell_pr, num))
            cpos += num

        return orders

    def run(self, state: TradingState) -> tuple[Dict[str, List[Order]], int, str]:
        """
        Entry point: For each time step, process the order depth for Resin and produce orders
        based on the short-spread market making/taking strategy.
        """
        orders = {}
        # Focus solely on Rainforest Resin.
        if "RAINFOREST_RESIN" in state.order_depths:
            od = state.order_depths["RAINFOREST_RESIN"]
            # Update our position based on the current state
            self.position["RAINFOREST_RESIN"] = state.position.get("RAINFOREST_RESIN", 0)
            # For a stable commodity like resin, we use the fair price as our acceptable bid and ask
            acc_bid = self.resin_fair_price
            acc_ask = self.resin_fair_price
            orders["RAINFOREST_RESIN"] = self.compute_orders_resin("RAINFOREST_RESIN", od, acc_bid, acc_ask)

        logger.flush(state, orders, 0, "")
        
        return orders, 0, ""
