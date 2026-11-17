# fbt — 期货 Tick 回测框架
## 行情文件放进 `data/` 文件夹
- 自动识别 snap 格式 (time / exchange_t / a_p1 / b_v1 / last_p / v ...), 时间转成北京时间, 以 exchange_t 为准。
- CSV 只读需要的列(45列->28列)
- 自动清洗: 买卖盘全空的垃圾行、买一>=卖一、集合竞价快照(08:55-09:00, 20:55-21:00); 丢弃行数记在 summary.csv 的 rows_dropped_in_file。
- 夜盘自动归属下一交易日(未处理法定节假日), 日盘/夜盘分别独立回测; 一个文件含多合约/多天也会自动切开。

## 快速开始
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

