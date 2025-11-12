# DesicAI - AI驱动的加密货币自动化交易系统

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

> 基于人工智能的加密货币量化交易系统，支持OKX交易所，集成实时数据采集、AI决策分析和自动化交易执行。

[English](README_EN.md) | 简体中文

## ✨ 特性

- 🤖 **AI驱动决策**：支持豆包、DeepSeek、通义千问等多种AI模型，智能分析市场行情
- 📊 **实时数据采集**：24小时不间断采集K线、订单簿、逐笔成交等实时行情数据
- 🎯 **自动化交易**：基于AI决策自动执行开仓、平仓、止盈止损等交易操作
- 🖥️ **Web管理界面**：直观的可视化界面，一键配置和启动所有组件
- 🔒 **风险管理**：多层止盈止损策略，智能仓位管理，保护资金安全
- 📈 **多周期分析**：结合短期（5分钟）和长期（4小时）多维度技术指标
- 💾 **数据持久化**：基于MySQL和Redis的高效数据存储方案
- 🌐 **代理支持**：支持HTTP代理，适配国内网络环境

## 📋 目录

- [交易所注册](#-交易所注册)
- [代部署服务](#-代部署服务)
- [系统架构](#-系统架构)
- [快速开始](#-快速开始)
- [安装部署](#-安装部署)
- [配置说明](#-配置说明)
- [使用指南](#-使用指南)
- [常见问题](#-常见问题)
- [贡献指南](#-贡献指南)
- [许可证](#-许可证)
- [免责声明](#-免责声明)

## 📝 交易所注册

在使用本系统前，您需要先注册交易所账户。通过我们的专属链接注册，您可以获得手续费返佣优惠！

### OKX 交易所

<div align="center">
</div>

**注册链接：** https://www.oyuzh.com/zh-hans/join/xiazhi?shortCode=6CngT5

**优势：**
- ✅ 手续费返佣优惠
- ✅ 流动性好，深度充足
- ✅ API稳定可靠
- ✅ 支持多种合约产品

**注册步骤：**
1. 点击上方链接访问注册页面
2. 输入邮箱/手机号注册账户
3. 完成身份认证（KYC）
4. 充值USDT到合约账户
5. 在 **个人中心** → **API** → **创建API** 获取API密钥

### Binance 币安

<div align="center"></div>

**注册链接：** https://www.binance.com/join?ref=XAZHI

**优势：**
- ✅ 手续费返佣优惠
- ✅ 全球最大交易所
- ✅ 交易品种丰富
- ✅ 支持多语言

**注册步骤：**
1. 点击上方链接访问注册页面
2. 输入邮箱/手机号注册账户
3. 完成身份认证（KYC）
4. 充值USDT到合约账户
5. 在 **API管理** 中创建API密钥

> **注意：** 目前系统主要支持OKX交易所，Binance支持正在开发中

---

## 💼 代部署服务

**对于不熟悉技术的小白用户，我们提供一对一的代部署服务！**

### 服务内容

- ✅ 完整环境搭建（Python + MySQL + Redis）
- ✅ 系统配置与调试
- ✅ API密钥配置
- ✅ 交易参数优化建议
- ✅ 使用培训与答疑
- ✅ 后期技术支持

### 联系方式

<div align="center">

**微信：lmyc11223344**

*添加时请备注：DesicAI部署*

</div>

---

## 🏗️ 系统架构

DesicAI 由三个核心组件组成：

```
┌─────────────────────────────────────────────────────────────┐
│                       Web管理界面                            │
│                    (spa_server.py)                          │
│            配置管理 | 实时监控 | 启动控制                     │
└──────────────────────┬──────────────────────────────────────┘
                       │
       ┌───────────────┴───────────────┐
       │                               │
┌──────▼──────┐              ┌────────▼────────┐
│  数据采集器  │              │   AI交易机器人   │
│ (standalone_ │◄────────────►│ (btc_enhanced_  │
│  data_       │   实时数据    │  trading_raw)   │
│  collector)  │              │                 │
└──────┬───────┘              └────────┬────────┘
       │                               │
       │  ┌──────────────────────┐     │
       └─►│   MySQL + Redis      │◄────┘
          │   数据存储层          │
          └──────────┬───────────┘
                     │
          ┌──────────▼───────────┐
          │    OKX Exchange      │
          │    交易所API          │
          └──────────────────────┘
```

### 核心组件

1. **Web管理界面** (`spa_server.py`)
   - 提供可视化配置和监控界面
   - 统一管理机器人和数据采集器的启停
   - 实时展示交易状态和历史记录

2. **数据采集器** (`standalone_data_collector.py`)
   - 24小时实时采集市场数据
   - 支持多交易对、多周期K线数据
   - 采集订单簿、逐笔成交等深度数据
   - 数据存储至MySQL和Redis

3. **AI交易机器人** (`examples/enhanced_trading.py`)
   - 从数据库读取实时行情数据
   - 调用AI模型进行智能决策分析
   - 执行自动化交易操作
   - 实时风险控制和仓位管理

## 🚀 快速开始

### 前置要求

- Python 3.8+
- OKX交易所账户（需开通API）

### 一键启动

```bash
# 1. 克隆项目
git clone https://github.com/xiazhi88/DesicAi.git
cd DesicAI

# 2. 运行环境安装脚本（自动检测并安装 venv、MySQL、Redis，创建 .env 配置文件）
python setup_environment.py

# 3. 激活虚拟环境
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/macOS

# 4. 安装 Python 依赖
pip install -r requirements.txt

# 5. 启动 Web 界面
python spa_server.py

# 6. 在浏览器中打开 http://localhost:1235
# 通过 Web 界面配置并启动数据采集器和交易机器人
```

## 📦 安装部署

### 通过 Web 界面启动（推荐）

```bash
# 启动 Web 管理界面
python spa_server.py

# 在浏览器中访问
http://localhost:1235

# 在 Web 界面中：
# 1. 配置所有参数
# 2. 点击"启动数据采集器"
# 3. 点击"启动交易机器人"
```

## ⚙️ 配置说明

### API配置

在OKX交易所创建API密钥：

1. 登录 [OKX官网](https://www.oyuzh.com/zh-hans/?shortCode=6CngT5)
2. 进入 **个人中心** → **API** → **创建API**
3. 设置权限：**读取**、**交易**（不需要提币权限）
4. 记录 `API Key`、`Secret Key`、`Passphrase`
5. 绑定IP白名单（可选，建议绑定）

**配置示例：**

![API配置流程](static/apiSet.png)

### AI模型配置

系统支持豆包、DeepSeek、通义千问等多种AI模型，**所有配置都可以通过 Web UI 界面完成**，无需手动编辑配置文件。

在 Web UI 中选择您的 AI 提供商并填入对应的 API Key 即可。

### 代理配置

如果在国内访问 OKX 需要代理，**可直接在 Web UI 界面中配置代理参数**：
- 代理地址
- 代理端口
- 认证信息（如需要）

### 交易参数

**所有交易参数都可以在 Web UI 中配置**，包括：
- 交易标的（如 BTC-USDT-SWAP）
- 杠杆倍数
- 数据新鲜度阈值
- 止盈止损设置

### 飞书通知（可选）

**飞书通知配置也可在 Web UI 中完成**：
- 启用/禁用飞书通知
- 配置 Webhook URL

## 📖 使用指南

### Web管理界面

启动后访问 `http://localhost:1235`

**功能列表：**

1. **配置管理**
   - API密钥配置
   - 交易参数设置
   - AI模型选择
   - 代理设置

2. **组件控制**
   - 启动/停止数据采集器
   - 启动/停止交易机器人
   - 查看运行状态

3. **数据监控**
   - 实时持仓查看
   - 交易历史记录
   - AI决策日志
   - 系统运行日志

4. **性能统计**
   - 收益率统计
   - 胜率分析
   - 回撤监控

## ❓ 常见问题

### Q1: AI返回JSON解析失败

**A:** AI响应格式可能不符合预期。检查：

1. AI模型配置是否正确
2. 查看日志中的完整AI响应
3. 尝试调整 `temperature` 参数

### Q2: 交易执行失败

**A:** 可能原因：

1. API权限不足（需要交易权限）
2. 余额不足
3. 订单参数不符合交易所规则
4. 市场波动导致价格偏移

查看详细日志：`logs/trading_bot_*.log`

### Q3: Web界面无法启动组件

**A:** 检查：

1. 端口是否被占用
2. Python路径是否正确
3. 查看Web服务日志




## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源协议。

## ⚠️ 免责声明

**重要提示：**

1. 本软件仅供学习和研究使用，不构成任何投资建议
2. 加密货币交易具有极高风险，可能导致全部本金损失
3. 使用本软件进行实盘交易的所有风险由用户自行承担
4. 作者不对使用本软件造成的任何损失负责
5. 请务必在充分了解风险的情况下谨慎使用
6. **本项目不支持模拟盘操作，所有交易均在实盘环境下进行**

**风险提示：**

- ⚠️ 杠杆交易风险极高，可能爆仓
- ⚠️ AI决策不保证盈利
- ⚠️ 市场波动可能导致巨额亏损
- ⚠️ 建议仅使用可承受损失的资金
- ⚠️ 建议先使用小额资金充分测试

## 📞 联系我们

- **微信**: lmyc11223344（代部署服务、技术支持，备注：DesicAI）
- **GitHub Issues**: [提交问题](https://github.com/xiazhi88/DesicAi/issues)
- **Email**: desicai@163.com

**交易所注册返佣链接：**
- OKX: https://www.oyuzh.com/zh-hans/join/xiazhi?shortCode=6CngT5
- Binance: https://www.binance.com/join?ref=XAZHI

## 🙏 致谢

感谢以下开源项目：

- [OKX API](https://www.okx.com/docs-v5/)
- [Python](https://www.python.org/)
- [Redis](https://redis.io/)
- [MySQL](https://www.mysql.com/)
- [Loguru](https://github.com/Delgan/loguru)

---

**如果这个项目对你有帮助，请给我们一个⭐Star⭐支持一下！**

Made with ❤️ by DesicAI Team
