"""基础类型: 方向/订单类型/订单/成交/Tick/仿真参数。时间统一为 int 毫秒时间戳。"""
from __future__ import annotations
from dataclasses import dataclass, field
import math

BUY, SELL = 1, -1
LIMIT, MARKET = "LIMIT", "MARKET"
GFD, FAK, FOK = "GFD", "FAK", "FOK"          # 当日有效 / 剩余撤销 / 全成或全撤
PENDING, NEW, PARTIAL, FILLED, CANCELLED, REJECTED = (
    "PENDING", "NEW", "PARTIAL", "FILLED", "CANCELLED", "REJECTED")
ACTIVE_STATUS = (PENDING, NEW, PARTIAL)


@dataclass(slots=True)
class Tick:
    ts: int
    instrument: str
    last: float
    volume: float            # 当日累计成交量
    oi: float
    bid_p: list
    bid_v: list
    ask_p: list
    ask_v: list
    upper: float = math.nan  # 涨停价
    lower: float = math.nan  # 跌停价

    @property
    def bid1(self): return self.bid_p[0] if self.bid_p else math.nan
    @property
    def ask1(self): return self.ask_p[0] if self.ask_p else math.nan
    @property
    def mid(self):
        if self.bid_p and self.ask_p:
            return (self.bid_p[0] + self.ask_p[0]) / 2
        return self.last


@dataclass(slots=True)
class Order:
    oid: int
    instrument: str
    side: int
    price: float
    qty: int
    otype: str = LIMIT
    tif: str = GFD
    status: str = PENDING
    filled: int = 0
    avg_price: float = 0.0
    send_ts: int = 0
    reason: str = ""
    queue_ahead: float = 0.0     # 仅交易所侧使用: 排在我们前面的量(含自己更早的挂单)

    @property
    def remaining(self): return self.qty - self.filled
    @property
    def active(self): return self.status in ACTIVE_STATUS


@dataclass(slots=True)
class Trade:
    oid: int
    instrument: str
    ts: int
    side: int
    price: float
    qty: int
    maker: bool
    fee: float


@dataclass
class SimConfig:
    # --- 延迟 (毫秒) ---
    submit_latency_ms: int = 5      # 策略发单 -> 到达交易所
    cancel_latency_ms: int = 5      # 策略撤单 -> 到达交易所
    md_latency_ms: int = 0          # 行情 交易所 -> 策略
    report_latency_ms: int = 0      # 回报 交易所 -> 策略
    # --- 策略驱动 ---
    timer_ms: int = 10              # on_timer 间隔, 0 关闭
    session_gap_ms: int = 60_000    # tick 间隔超过它视为休市, 休市期间不触发 on_timer
    # --- 排队假设 ---
    queue_ratio: float = 1.0        # 挂单时前面排队量 = ratio * 该价位显示量; 1=队尾, 0=队首
    cancel_model: str = "prob"      # 盘口量减少(撤单)对队列的影响: none / min / prob
    # --- 费用 ---
    fee_rate: float = 0.0           # 按成交金额比例
    fee_per_lot: float = 0.0        # 每手固定费用
    # --- 其它 ---
    equity_sample_ms: int = 60_000
