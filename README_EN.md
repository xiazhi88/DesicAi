# DesicAI - AI-Powered Cryptocurrency Automated Trading System

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

> AI-based cryptocurrency quantitative trading system supporting OKX exchange, integrating real-time data collection, AI decision analysis, and automated trade execution.

English | [简体中文](README.md)

## ✨ Features

- 🤖 **AI-Driven Decisions**: Support for multiple AI models including Doubao, DeepSeek, Tongyi Qianwen for intelligent market analysis
- 📊 **Real-time Data Collection**: 24/7 continuous collection of K-line, order book, and tick-by-tick trading data
- 🎯 **Automated Trading**: Automatic execution of opening/closing positions, take-profit/stop-loss based on AI decisions
- 🖥️ **Web Management Interface**: Intuitive visual interface for one-click configuration and startup of all components
- 🔒 **Risk Management**: Multi-layer take-profit/stop-loss strategy, intelligent position management, capital protection
- 📈 **Multi-Timeframe Analysis**: Combining short-term (5-minute) and long-term (4-hour) multi-dimensional technical indicators
- 💾 **Data Persistence**: Efficient data storage solution based on MySQL and Redis
- 🌐 **Proxy Support**: HTTP proxy support, adapted for domestic network environments

## 📋 Table of Contents

- [Exchange Registration](#-exchange-registration)
- [Deployment Services](#-deployment-services)
- [System Architecture](#-system-architecture)
- [Quick Start](#-quick-start)
- [Installation & Deployment](#-installation--deployment)
- [Configuration](#-configuration)
- [User Guide](#-user-guide)
- [FAQ](#-faq)
- [Contributing](#-contributing)
- [License](#-license)
- [Disclaimer](#-disclaimer)

## 📝 Exchange Registration

Before using this system, you need to register an exchange account. By registering through our exclusive links, you can get trading fee rebates!

### OKX Exchange

<div align="center">
</div>

**Registration Link:** https://www.oyuzh.com/zh-hans/join/xiazhi?shortCode=6CngT5

**Advantages:**
- ✅ Trading fee rebates
- ✅ Good liquidity and depth
- ✅ Stable and reliable API
- ✅ Support for multiple contract products

**Registration Steps:**
1. Click the link above to visit the registration page
2. Register with email/phone number
3. Complete identity verification (KYC)
4. Deposit USDT to futures account
5. Go to **Profile** → **API** → **Create API** to obtain API keys

### Binance

<div align="center"></div>

**Registration Link:** https://www.binance.com/join?ref=XAZHI

**Advantages:**
- ✅ Trading fee rebates
- ✅ World's largest exchange
- ✅ Rich variety of trading pairs
- ✅ Multi-language support

**Registration Steps:**
1. Click the link above to visit the registration page
2. Register with email/phone number
3. Complete identity verification (KYC)
4. Deposit USDT to futures account
5. Create API keys in **API Management**

> **Note:** Currently the system mainly supports OKX exchange, Binance support is under development

---

## 💼 Deployment Services

**For non-technical users, we provide one-on-one deployment services!**

### Service Content

- ✅ Complete environment setup (Python + MySQL + Redis)
- ✅ System configuration and debugging
- ✅ API key configuration
- ✅ Trading parameter optimization recommendations
- ✅ Usage training and Q&A
- ✅ Ongoing technical support

### Contact

<div align="center">

**WeChat: lmyc11223344**

*Please note: DesicAI Deployment when adding*

</div>

---

## 🏗️ System Architecture

DesicAI consists of three core components:

```
┌─────────────────────────────────────────────────────────────┐
│                    Web Management Interface                 │
│                      (spa_server.py)                        │
│        Configuration Management | Monitoring | Control      │
└──────────────────────┬──────────────────────────────────────┘
                       │
       ┌───────────────┴───────────────┐
       │                               │
┌──────▼──────┐              ┌────────▼────────┐
│ Data         │              │  AI Trading Bot │
│ Collector    │◄────────────►│ (btc_enhanced_  │
│ (standalone_ │  Real-time   │  trading_raw)   │
│  data_       │    Data      │                 │
│  collector)  │              │                 │
└──────┬───────┘              └────────┬────────┘
       │                               │
       │  ┌──────────────────────┐     │
       └─►│   MySQL + Redis      │◄────┘
          │   Data Storage       │
          └──────────┬───────────┘
                     │
          ┌──────────▼───────────┐
          │    OKX Exchange      │
          │    Exchange API      │
          └──────────────────────┘
```

### Core Components

1. **Web Management Interface** (`spa_server.py`)
   - Provides visual configuration and monitoring interface
   - Unified management of bot and data collector start/stop
   - Real-time display of trading status and history

2. **Data Collector** (`standalone_data_collector.py`)
   - 24/7 real-time market data collection
   - Support for multiple trading pairs and multi-timeframe K-line data
   - Collection of order book, tick-by-tick trades and depth data
   - Data storage to MySQL and Redis

3. **AI Trading Bot** (`examples/enhanced_trading.py`)
   - Read real-time market data from database
   - Call AI models for intelligent decision analysis
   - Execute automated trading operations
   - Real-time risk control and position management

## 🚀 Quick Start

### Prerequisites

- Python 3.8+
- OKX exchange account (API enabled)

### One-Click Startup

```bash
# 1. Clone the project
git clone https://github.com/yourusername/DesicAI.git
cd DesicAI

# 2. Run environment installation script (auto-detects and installs venv, MySQL, Redis, creates .env config)
python setup_environment.py

# 3. Activate virtual environment
venv\Scripts\activate  # Windows
source venv/bin/activate  # Linux/macOS

# 4. Install Python dependencies
pip install -r requirements.txt

# 5. Start Web interface
python spa_server.py

# 6. Open http://localhost:1235 in browser
# Configure and start data collector and trading bot through Web interface
```

## 📦 Installation & Deployment

### Launch via Web Interface (Recommended)

```bash
# Start Web management interface
python spa_server.py

# Visit in browser
http://localhost:1235

# In the Web interface:
# 1. Configure all parameters
# 2. Click "Start Data Collector"
# 3. Click "Start Trading Bot"
```

## ⚙️ Configuration

### API Configuration

Create API keys on OKX exchange:

1. Login to [OKX Official Website](https://www.okx.com)
2. Go to **Profile** → **API** → **Create API**
3. Set permissions: **Read**, **Trade** (withdrawal permission not needed)
4. Record `API Key`, `Secret Key`, `Passphrase`
5. Bind IP whitelist (optional, recommended)

**Configuration Example:**

![API Configuration Process](frontend/apiSet.png)

### AI Model Configuration

The system supports multiple AI models including Doubao, DeepSeek, Tongyi Qianwen. **All configurations can be completed through the Web UI interface**, no need to manually edit configuration files.

Simply select your AI provider in the Web UI and enter the corresponding API Key.

### Proxy Configuration

If you need a proxy to access OKX from China, **you can configure proxy parameters directly in the Web UI interface**:
- Proxy address
- Proxy port
- Authentication info (if needed)

### Trading Parameters

**All trading parameters can be configured in the Web UI**, including:
- Trading instrument (e.g., BTC-USDT-SWAP)
- Leverage multiplier
- Data freshness threshold
- Take-profit/stop-loss settings

### Feishu Notifications (Optional)

**Feishu notification configuration can also be completed in the Web UI**:
- Enable/disable Feishu notifications
- Configure Webhook URL

## 📖 User Guide

### Web Management Interface

Visit `http://localhost:1235` after startup

**Feature List:**

1. **Configuration Management**
   - API key configuration
   - Trading parameter settings
   - AI model selection
   - Proxy settings

2. **Component Control**
   - Start/stop data collector
   - Start/stop trading bot
   - View running status

3. **Data Monitoring**
   - Real-time position view
   - Trading history
   - AI decision logs
   - System operation logs

4. **Performance Statistics**
   - Return rate statistics
   - Win rate analysis
   - Drawdown monitoring

## ❓ FAQ

### Q1: AI JSON parsing failed

**A:** AI response format may not meet expectations. Check:

1. Is AI model configuration correct
2. Review complete AI response in logs
3. Try adjusting `temperature` parameter

### Q2: Trade execution failed

**A:** Possible reasons:

1. Insufficient API permissions (trading permission required)
2. Insufficient balance
3. Order parameters don't comply with exchange rules
4. Market volatility causing price deviation

Check detailed logs: `logs/trading_bot_*.log`

### Q3: Web interface cannot start components

**A:** Check:

1. Port conflicts
2. Python path is correct
3. Review Web service logs

## 📄 License

This project is licensed under the [MIT License](LICENSE).

## ⚠️ Disclaimer

**Important Notice:**

1. This software is for learning and research purposes only, does not constitute investment advice
2. Cryptocurrency trading carries extremely high risks and may result in total capital loss
3. All risks from using this software for live trading are borne by the user
4. The author is not responsible for any losses caused by using this software
5. Please use with caution only after fully understanding the risks
6. **This project does not support paper trading; all trades are executed in live environment**

**Risk Warning:**

- ⚠️ Leveraged trading carries extremely high risk, may result in liquidation
- ⚠️ AI decisions do not guarantee profits
- ⚠️ Market volatility may cause huge losses
- ⚠️ Recommend using only capital you can afford to lose
- ⚠️ Recommend thorough testing with small amounts first

## 📞 Contact Us

- **WeChat**: lmyc11223344 (Deployment services, technical support, note: DesicAI)
- **GitHub Issues**: [Submit Issue](https://github.com/yourusername/desicAI/issues)
- **Email**: desicai@163.com

**Exchange Registration Referral Links:**
- OKX: https://www.oyuzh.com/zh-hans/join/xiazhi?shortCode=6CngT5
- Binance: https://www.binance.com/join?ref=XAZHI

## 🙏 Acknowledgments

Thanks to the following open source projects:

- [OKX API](https://www.okx.com/docs-v5/)
- [Python](https://www.python.org/)
- [Redis](https://redis.io/)
- [MySQL](https://www.mysql.com/)
- [Loguru](https://github.com/Delgan/loguru)

---

**If this project helps you, please give us a ⭐Star⭐ for support!**

Made with ❤️ by DesicAI Team
