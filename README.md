# ETF 超短线信号系统 · 云端备份仓库

这是一套**在 A 股上市 ETF（港股通 / 美股 QDII / 商品期货 / T+0）的超短线信号系统**。
服务端在交易时段每分钟扫描，信号**触发即推微信**，消息带买入价、目标价、止损价；
另有一个安卓看板 APP。本仓库每天自动更新，用来**完整备份并方便他人/其他 AI 接手**。

> 想先搞懂交易规则，直接看 **[`战法说明书.md`](战法说明书.md)**。

## 目录结构

```
.
├── README.md                 # 本文件（怎么跑、怎么接手）
├── 战法说明书.md             # 交易规则、常量、信号逻辑（先看这个）
├── requirements.txt          # Python 依赖
├── server/                   # 服务端（信号扫描 + 推送）
│   ├── scan_pm.py            # 主程序（信号逻辑都在这里）
│   ├── quote_util.py         # 行情取数（腾讯/新浪，防陈旧）
│   ├── notify_push.py        # 推送（WxPusher/ntfy 等）
│   ├── scan_pm_supervisord.conf  # 守护进程配置
│   ├── notify_config.example.json # 推送配置空模板（脱敏，复制后填自己的）
│   ├── hk_etf_list.json      # ETF 清单
│   ├── etf_holdings_weighted.json  # 成分股权重
│   ├── commodity_holdings.json
│   └── prev_amt.json
├── app/                      # 安卓看板工程（含完整源码）
│   ├── assets/index.html     # 看板单文件（核心前端，7MB）
│   ├── build.sh              # 一键打包 APK 脚本
│   ├── AndroidManifest.xml
│   ├── src/                  # Java 源码
│   └── hms_libs/             # 华为推送依赖（当前方案未启用，留作参考）
├── backtest/                 # 回测脚本
│   ├── backtest_planB*.py    # 方案 B 坑反弹迭代版本
│   └── backtest_final*.py
└── data/
    └── archive/              # 干净的分钟线数据（按日期）
        ├── 20261117 … 20261120
        └── intraday/         # 盘中累积
```

## 环境要求

- Python 3.12（3.10+ 应该也能跑），依赖见 `requirements.txt`（实际只需 `requests`）。
- 打包 APK 另需：JDK 17、Android SDK（platform android-12、build-tools 33.0.0）、debug.keystore。
- 守护进程可选：supervisor（`pip install supervisor`）。

## 快速开始（跑服务端）

```bash
# 1. 装依赖
pip install -r requirements.txt

# 2. 准备推送配置（复制空模板，填入自己的密钥）
cd server
cp notify_config.example.json notify_config.json
#   然后编辑 notify_config.json，把 wxpusher.spt 等填上

# 3. 运行
python3 scan_pm.py            # 单次扫描（手动用）
python3 scan_pm.py --loop     # 常驻：每分钟循环，14:50 自动退出
python3 scan_pm.py --ensure   # 看门狗：常驻不在就后台拉起
```

用 supervisor 托管（崩溃自动重启）：

```bash
# 注意：scan_pm_supervisord.conf 里的路径是原机器的绝对路径，
# 换机器后要先把里面的路径改成你自己的，再执行：
supervisord -c scan_pm_supervisord.conf
supervisorctl -c scan_pm_supervisord.conf status
```

## 打包安卓 APK

```bash
cd app
# build.sh 里的 JAVA_HOME、SDK、keystore 路径是原机器的，
# 换机器后先按自己环境改 build.sh，再：
bash build.sh
# 产物：港股ETF看板.apk，脚本最后会自动验证签名
```

## 数据说明

- `data/archive/YYYYMMDD/` 下每个交易日含 `m1`（1 分钟 K）、`m5`、`day`、`auction`（竞价）等。
- 分钟 K 按时间戳去重累积，只保留当日根；接口取不到时保留已有数据，**不估算、不 0 填充**。
- 涨幅、红率一律以**开盘价**为基准；陈旧帧红率标"暂缺"，不当 0%。

## 给接手者（其他豆包 / 开发者）

1. 先读 **`战法说明书.md`**，理解品种、时段、信号类型和全部常量。
2. 服务端逻辑入口是 `server/scan_pm.py`：从 `scan_once()`（主流程）、`gate_eval()`（信号判定）、
   `entry_levels()`（三个价格计算）、`candidate_metrics()`（候选门槛）这几个函数读起。
3. 前端看板是单文件 `app/assets/index.html`，里面的常量与服务端对齐。
4. 想复现回测，用 `backtest/` 里的脚本配合 `data/archive` 的分钟线。
5. 本仓库**不含任何推送密钥**，需要自己申请 WxPusher 的 spt 后填进配置。

## 安全说明

- 推送密钥（WxPusher spt、华为 client_secret、ntfy topic 等）**从未提交到本仓库**，
  仓库里只有清空的 `notify_config.example.json`。
- 仓库当前为**公开**，方便其他 AI 直接读取；内容仅为 ETF 行情与代码，不含个人隐私数据。
- 若要改成私有仓库，接手的 AI 需要带访问令牌才能读取。
