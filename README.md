# fbt — 期货 Tick 回测框架 (第一阶段, 纯 Python, 本地运行)

## 你的数据 (snap_*.csv / .xlsx / .parquet) 直接可用
把行情文件放进 `data/` 文件夹, 然后:
```bash
pip install pandas numpy openpyxl
cd examples
python run_demo.py            # 默认读 ../data 下所有 csv/xlsx/parquet; 也可 python run_demo.py 文件或文件夹
```
- 自动识别 snap 格式 (time / exchange_t / a_p1 / b_v1 / last_p / v ...), 时间转成北京时间, 以 exchange_t 为准。
- CSV 只读需要的列(45列->28列), 8万行约 0.3 秒。
- 自动清洗: 买卖盘全空的垃圾行、买一>=卖一、集合竞价快照(08:55-09:00, 20:55-21:00); 丢弃行数记在 summary.csv 的 rows_dropped_in_file。
- 夜盘自动归属下一交易日(未处理法定节假日), 日盘/夜盘分别独立回测; 一个文件含多合约/多天也会自动切开。
- 大文件建议先转 parquet: `python tools/convert_to_parquet.py 原目录 新目录` (需 `pip install pyarrow`)。

## 快速开始 (合成数据)
```bash
pip install pandas numpy              # Python >= 3.10
python tests_matching.py              # 撮合规则单元测试
cd examples
python gen_data.py /tmp/syn 4         # 生成合成数据(仅验证流程)
python run_demo.py /tmp/syn               # 并行回测, 结果在 out/summary.csv 与 out/trades/
```

## 目录
| 文件 | 作用 |
|---|---|
| fbt/strategy.py | **交易员模板**: 继承 `Strategy`, 重写 on_tick / on_timer / on_order / on_trade, 用 buy/sell/cancel_order |
| fbt/matching.py | Tick 撮合模拟: 盘口插值、排队、延迟后成交、FOK/FAK/市价单 |
| fbt/engine.py | 事件驱动引擎 (单合约单日), 延迟用事件堆实现 |
| fbt/runner.py | 并行调度: 一个行情文件(一个交易日)=一个任务, ProcessPoolExecutor |
| fbt/contracts.py | 合约乘数/最小变动价位 + 各交易所下单规则 (**需核对**) |
| fbt/data.py | 读 csv/xlsx/parquet + snap 格式适配 + 数据清洗, 列名映射(colmap), 清洗空档(DBL_MAX) |
| examples/simple_mm.py | 示例做市策略 |

## 接入真实数据
一个文件 = 一个交易日(可含多个合约), 列名不同就传 `colmap={"ts": "你的时间列", "last_price": "...", ...}`,
标准列名见 fbt/data.py 文件头注释。`SimConfig` 里调延迟、队首/队尾(queue_ratio)、撤单模型、手续费。

## 需要和交易员确认的点
1. contracts.py 里的乘数/最小变动价位/各所是否支持市价单、单笔上限, 都按我的理解填的默认值, 请对照撮合规则表改。
2. 成交只用 `volume` 差分 + `last_price` 推断, 同一 tick 内价格来回穿越会被忽略。
3. 各交易日独立回测, 日初仓位 0、日末撤单并按最后中间价估值。
4. 未实现: 自成交限制、开平/平今区分、保证金、集合竞价。

## 迁移 C++ 的建议
Strategy 对外只有 5 个回调 + send_order/cancel_order + pos/now/last_tick, 到 C++ 时在引擎侧实现同样的接口,
用 pybind11 把 C++ 引擎暴露给 Python 策略(Python 策略类不变), 撮合逻辑 matching.py 逐函数对译即可。
