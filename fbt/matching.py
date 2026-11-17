"""仅用 Tick 的撮合模拟器 (单合约)。

核心假设 (都可在 SimConfig 里调):
 1. 两个 tick 之间盘口保持上一个 tick 的快照不变 (阶梯式), 订单到达交易所时看到的就是最近一个 tick 的盘口。
 2. 主动单(可成交价)按快照档位吃单, 同一快照内自己吃过的量会被扣除, 不会重复吃。
 3. 被动单挂入队列, 前方排队量 = queue_ratio * 该价位显示量 (+ 自己更早的同价挂单)。
 4. 被动单的成交来自下一个 tick 的成交量增量 dv(=volume差) 与成交价 last:
      - 先按 tick 规则/盘口判断主动方向 (买方主动 -> 打到卖盘, 我方卖单可能成交);
      - 我方价格比成交价更“优”(穿价) -> 全部成交;
      - 我方价格 == 成交价 -> 用 (dv - 更优价位被吃掉的量) 作为预算, 先消耗前方排队, 再成交我方;
      - 盘口该价位量减少(撤单)按 cancel_model 推进队列: none / min / prob(按比例)。
"""
from __future__ import annotations
import math
from .core import *
from .contracts import ContractSpec, ExchangeRule


class SimExchange:
    def __init__(self, spec: ContractSpec, rule: ExchangeRule, cfg: SimConfig, emit):
        self.spec, self.rule, self.cfg, self.emit = spec, rule, cfg, emit
        self.book: Tick | None = None
        self.rest = {BUY: {}, SELL: {}}      # side -> price -> [Order] (时间优先)
        self.orders: dict[int, Order] = {}   # 交易所侧存活订单
        self.consumed: dict = {}             # (side, price) -> 当前快照内被我方吃掉的量
        self.pos = 0
        self.cash = 0.0
        self.fees = 0.0
        self.volume = 0
        self.maker_volume = 0
        self.max_abs_pos = 0
        self.n_orders = 0
        self.n_rejected = 0
        self.trades: list[Trade] = []
        self._last_budget = (0, math.nan, 0.0)

    # ------------------------------------------------------------------ 行情
    def on_tick(self, tick: Tick):
        prev, self.book = self.book, tick
        self.consumed.clear()
        if prev is None or not (self.rest[BUY] or self.rest[SELL]):
            return
        dv = tick.volume - prev.volume
        if dv < 0:
            dv = tick.volume
        self._last_budget = (0, math.nan, 0.0)
        if dv > 0:
            self._on_trades(prev, tick, dv)
        self._update_queues(prev, tick)

    @staticmethod
    def _aggressor(prev: Tick, tick: Tick) -> int:
        lp = tick.last
        if prev.ask_p and lp >= prev.ask_p[0]: return BUY
        if prev.bid_p and lp <= prev.bid_p[0]: return SELL
        if lp > prev.last: return BUY
        if lp < prev.last: return SELL
        return BUY if lp >= tick.mid else SELL

    def _on_trades(self, prev: Tick, tick: Tick, dv: float):
        lp = tick.last
        if self._aggressor(prev, tick) == BUY:
            side = SELL
            better = sum(v for p, v in zip(prev.ask_p, prev.ask_v) if p < lp)
        else:
            side = BUY
            better = sum(v for p, v in zip(prev.bid_p, prev.bid_v) if p > lp)
        budget = max(0.0, dv - better)
        self._last_budget = (side, lp, budget)
        book = self.rest[side]
        for px in list(book.keys()):
            lst = book[px]
            through = (px < lp) if side == SELL else (px > lp)
            if through:                      # 成交价已穿过我方价位 -> 全部成交(按我方挂单价)
                for o in list(lst):
                    self._maker_fill(o, o.remaining, px, tick.ts)
            elif px == lp:
                for o in list(lst):
                    a = o.queue_ahead
                    f = int(min(o.remaining, max(0.0, budget - a)))
                    o.queue_ahead = max(0.0, a - budget)
                    if f > 0:
                        self._maker_fill(o, f, px, tick.ts)

    def _update_queues(self, prev: Tick, tick: Tick):
        if self.cfg.cancel_model == "none":
            return
        bside, blp, budget = self._last_budget
        for side in (BUY, SELL):
            if not self.rest[side]:
                continue
            if side == BUY:
                old = dict(zip(prev.bid_p, prev.bid_v)); new = dict(zip(tick.bid_p, tick.bid_v))
            else:
                old = dict(zip(prev.ask_p, prev.ask_v)); new = dict(zip(tick.ask_p, tick.ask_v))
            for px, lst in self.rest[side].items():
                if px not in old or px not in new:
                    continue
                traded = min(budget, old[px]) if (bside == side and px == blp) else 0.0
                rem_old = old[px] - traded
                if rem_old <= 0 or new[px] >= rem_old:
                    continue
                ratio = new[px] / rem_old
                own = 0.0
                for o in lst:
                    ext = max(0.0, o.queue_ahead - own)
                    ext = ext * ratio if self.cfg.cancel_model == "prob" else min(ext, new[px])
                    o.queue_ahead = ext + own
                    own += o.remaining

    # ------------------------------------------------------------------ 订单
    def _validate(self, o: Order) -> str:
        r, bk = self.rule, self.book
        if bk is None: return "NO_MARKET_DATA"
        if o.qty <= 0: return "BAD_QTY"
        if o.otype == LIMIT:
            t = self.spec.tick_size
            if abs(round(o.price / t) * t - o.price) > 1e-6 * t: return "PRICE_NOT_ON_TICK"
            if not math.isnan(bk.upper) and o.price > bk.upper + 1e-9: return "ABOVE_UPPER_LIMIT"
            if not math.isnan(bk.lower) and o.price < bk.lower - 1e-9: return "BELOW_LOWER_LIMIT"
            if r.max_limit_qty and o.qty > r.max_limit_qty: return "QTY_EXCEEDS_LIMIT_MAX"
        elif o.otype == MARKET:
            if not r.allow_market: return "MARKET_ORDER_NOT_SUPPORTED"
            if o.tif not in r.market_tifs: return "MARKET_TIF_NOT_SUPPORTED"
            if r.max_market_qty and o.qty > r.max_market_qty: return "QTY_EXCEEDS_MARKET_MAX"
        else:
            return "BAD_ORDER_TYPE"
        if o.tif == FAK and not r.allow_fak: return "FAK_NOT_SUPPORTED"
        if o.tif == FOK and not r.allow_fok: return "FOK_NOT_SUPPORTED"
        if o.tif not in (GFD, FAK, FOK): return "BAD_TIF"
        return ""

    def on_new_order(self, o: Order, ts: int):
        self.n_orders += 1
        reason = self._validate(o)
        if reason:
            o.status, o.reason = REJECTED, reason
            self.n_rejected += 1
            self._report_order(o, ts)
            return
        bk = self.book
        if o.side == BUY:
            prices, vols, opp = bk.ask_p, bk.ask_v, SELL
            ok = lambda p: o.otype == MARKET or p <= o.price
        else:
            prices, vols, opp = bk.bid_p, bk.bid_v, BUY
            ok = lambda p: o.otype == MARKET or p >= o.price
        levels = []
        for p, v in zip(prices, vols):
            if not ok(p): break
            a = v - self.consumed.get((opp, p), 0)
            if a > 0: levels.append((p, a))
        if o.tif == FOK and sum(a for _, a in levels) < o.qty:
            o.status, o.reason = CANCELLED, "FOK_NOT_FILLED"
            self._report_order(o, ts)
            return
        for p, a in levels:
            if o.remaining <= 0: break
            q = int(min(a, o.remaining))
            self.consumed[(opp, p)] = self.consumed.get((opp, p), 0) + q
            self._fill(o, q, p, ts, maker=False)
        if o.remaining > 0:
            if o.otype == LIMIT and o.tif == GFD:
                if o.filled == 0: o.status = NEW
                self._rest(o)
            else:
                o.status = CANCELLED
                o.reason = "NO_LIQUIDITY" if o.filled == 0 else "REMAINDER_CANCELLED"
        self._report_order(o, ts)

    def _rest(self, o: Order):
        bk, px = self.book, o.price
        if o.side == BUY: prices, vols = bk.bid_p, bk.bid_v
        else: prices, vols = bk.ask_p, bk.ask_v
        vis = 0.0
        if prices:
            better = (px > prices[0]) if o.side == BUY else (px < prices[0])
            worse = (px < prices[-1]) if o.side == BUY else (px > prices[-1])
            if better: vis = 0.0
            elif worse: vis = vols[-1]
            else:
                d = dict(zip(prices, vols)); vis = d.get(px, 0.0)
        lst = self.rest[o.side].setdefault(px, [])
        o.queue_ahead = self.cfg.queue_ratio * vis + sum(x.remaining for x in lst)
        lst.append(o)
        self.orders[o.oid] = o

    def on_cancel(self, oid: int, ts: int):
        o = self.orders.get(oid)
        if o is None:
            self.emit(ts, ("cancel_reject", oid, "ORDER_NOT_FOUND_OR_DONE"))
            return
        self._remove(o)
        o.status, o.reason = CANCELLED, "USER_CANCEL"
        self._report_order(o, ts)

    def _remove(self, o: Order):
        lst = self.rest[o.side].get(o.price, [])
        if o in lst:
            idx = lst.index(o)
            for x in lst[idx + 1:]:                      # 自己撤掉后, 后面自己的单排队前移
                x.queue_ahead = max(0.0, x.queue_ahead - o.remaining)
            lst.remove(o)
            if not lst: del self.rest[o.side][o.price]
        self.orders.pop(o.oid, None)

    def cancel_all(self, ts: int):
        for o in list(self.orders.values()):
            self._remove(o); o.status, o.reason = CANCELLED, "EOD"
            self._report_order(o, ts)

    # ------------------------------------------------------------------ 成交
    def _maker_fill(self, o: Order, qty: int, price: float, ts: int):
        self._fill(o, qty, price, ts, maker=True)
        if o.remaining <= 0:
            self._remove(o)
        self._report_order(o, ts)

    def _fill(self, o: Order, qty: int, price: float, ts: int, maker: bool):
        if qty <= 0: return
        fee = qty * self.spec.fee_per_lot + price * qty * self.spec.multiplier * self.spec.fee_rate
        fee += qty * self.cfg.fee_per_lot + price * qty * self.spec.multiplier * self.cfg.fee_rate
        o.avg_price = (o.avg_price * o.filled + price * qty) / (o.filled + qty)
        o.filled += qty
        o.status = FILLED if o.remaining <= 0 else PARTIAL
        self.pos += o.side * qty
        self.cash -= o.side * price * qty * self.spec.multiplier
        self.fees += fee
        self.volume += qty
        if maker: self.maker_volume += qty
        self.max_abs_pos = max(self.max_abs_pos, abs(self.pos))
        t = Trade(o.oid, o.instrument, ts, o.side, price, qty, maker, fee)
        self.trades.append(t)
        self.emit(ts, ("trade", t))

    def _report_order(self, o: Order, ts: int):
        self.emit(ts, ("order", o.oid, o.status, o.filled, o.avg_price, o.reason))
        if o.status in (FILLED, CANCELLED, REJECTED):
            self.orders.pop(o.oid, None)
