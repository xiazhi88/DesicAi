"""
OKX 配置管理
"""
import os
from typing import Optional
from dotenv import load_dotenv

# 加载环境变量
load_dotenv('.env')


class Config:
    """配置类"""

    def __init__(self):
        """从环境变量加载配置"""
        # API 配置
        self.API_KEY = os.getenv('OKX_API_KEY', '')
        self.SECRET_KEY = os.getenv('OKX_SECRET_KEY', '')
        self.PASSPHRASE = os.getenv('OKX_PASSPHRASE', '')

        # 环境配置
        self.IS_DEMO = os.getenv('OKX_IS_DEMO', 'false').lower() == 'true'
        self.USE_AWS = os.getenv('OKX_USE_AWS', 'false').lower() == 'true'
        
        self.INTERVAL_MINUTES = os.getenv('INTERVAL_MINUTES', '3')

        # ============ AI 配置 ============

        # AI 提供商选择（doubao / deepseek / qwen / custom）
        self.AI_PROVIDER = os.getenv('AI_PROVIDER', 'doubao')  # doubao / deepseek / qwen / custom

        # 豆包API配置
        self.DOUBAO_API_KEY = os.getenv('DOUBAO_API_KEY', '')
        self.DOUBAO_ENDPOINT_ID = os.getenv('DOUBAO_ENDPOINT_ID', '')
        self.DOUBAO_MODEL = os.getenv('DOUBAO_MODEL', 'doubao-seed-1-6-lite-251015')
        # 可选模型：doubao-seed-1-6-lite-251015, doubao-seed-1-6-flash-250828,
        #          doubao-seed-1-6-thinking-250715, doubao-seed-1-6-251015
        self.DOUBAO_TIMEOUT = int(os.getenv('DOUBAO_TIMEOUT', '10'))  # AI调用超时（秒）

        # DeepSeek API配置
        self.DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
        self.DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')
        # 可选模型：deepseek-chat, deepseek-reasoner
        self.DEEPSEEK_TIMEOUT = int(os.getenv('DEEPSEEK_TIMEOUT', '10'))  # AI调用超时（秒）

        # 通义千问API配置
        self.QWEN_API_KEY = os.getenv('QWEN_API_KEY', '')
        self.QWEN_MODEL = os.getenv('QWEN_MODEL', 'qwen-plus')
        # 可选模型：qwen-plus, qwen-turbo, qwen-max, qwen-max-longcontext
        self.QWEN_TIMEOUT = int(os.getenv('QWEN_TIMEOUT', '10'))  # AI调用超时（秒）

        # 其他 OpenAI-compatible API 配置
        self.CUSTOM_AI_API_KEY = os.getenv('CUSTOM_AI_API_KEY', '')
        self.CUSTOM_AI_BASE_URL = os.getenv('CUSTOM_AI_BASE_URL', '')
        self.CUSTOM_AI_MODEL = os.getenv('CUSTOM_AI_MODEL', '')
        self.CUSTOM_AI_TIMEOUT = int(os.getenv('CUSTOM_AI_TIMEOUT', '20'))  # AI调用超时（秒）

        # 日志配置
        self.LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

        # 机器人启动时间（用于AI理解长期任务）
        self.BOT_START_TIME = os.getenv('BOT_START_TIME', '')  # 格式：2025-10-28 00:00:00

        # ============ MySQL数据库配置 ============
        self.MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
        self.MYSQL_PORT = int(os.getenv('MYSQL_PORT', '33006'))
        self.MYSQL_USER = os.getenv('MYSQL_USER', 'root')
        self.MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
        self.MYSQL_DATABASE = os.getenv('MYSQL_DATABASE', 'trading_data')

        # ============ 交易配置 ============

        # 交易标的
        self.INST_ID = os.getenv('INST_ID', 'BTC-USDT-SWAP')

        # 默认杠杆倍数
        self.DEFAULT_LEVERAGE = int(os.getenv('DEFAULT_LEVERAGE', '5'))

        # ============ 代理配置 ============
        self.PROXY_ENABLED=os.getenv('PROXY_ENABLED', 'false') =='true'
        self.PROXY_TYPE=os.getenv('PROXY_TYPE', 'http')
        self.PROXY_HOST=os.getenv('PROXY_HOST',  '127.0.0.1')
        self.PROXY_PORT=os.getenv('PROXY_PORT',  '7890')
        self.PROXY_USERNAME=os.getenv('PROXY_USERNAME',  '')
        self.PROXY_PASSWORD=os.getenv('PROXY_PASSWORD',  '')

        # ============ 数据采集器配置 ============
        self.COLLECTOR_SYMBOLS = os.getenv('COLLECTOR_SYMBOLS', 'BTC-USDT-SWAP,ETH-USDT-SWAP')
        self.COLLECTOR_TIMEFRAMES = os.getenv('COLLECTOR_TIMEFRAMES', '1m,5m,15m,30m,1H,4H')
        self.COLLECTOR_HISTORY_DAYS = int(os.getenv('COLLECTOR_HISTORY_DAYS', '30'))
        self.COLLECTOR_STATUS_INTERVAL = int(os.getenv('COLLECTOR_STATUS_INTERVAL', '20'))
        self.COLLECTOR_DATA_TIMEOUT = int(os.getenv('COLLECTOR_DATA_TIMEOUT', '180'))

        # ============ 飞书机器人配置 ============
        self.FEISHU_ENABLED = os.getenv('FEISHU_ENABLED', 'false').lower() == 'true'
        self.FEISHU_WEBHOOK_URL = os.getenv('FEISHU_WEBHOOK_URL', '') 


    def is_configured(self) -> bool:
        """检查是否已配置API密钥"""
        return bool(self.API_KEY and self.SECRET_KEY and self.PASSPHRASE)

    def is_ai_configured(self) -> bool:
        """检查AI是否已配置"""
        if self.AI_PROVIDER == 'doubao':
            return bool(self.DOUBAO_API_KEY and self.DOUBAO_ENDPOINT_ID)
        elif self.AI_PROVIDER == 'deepseek':
            return bool(self.DEEPSEEK_API_KEY)
        elif self.AI_PROVIDER == 'qwen':
            return bool(self.QWEN_API_KEY)
        elif self.AI_PROVIDER == 'custom':
            return bool(self.CUSTOM_AI_BASE_URL and self.CUSTOM_AI_MODEL)
        return False

    def get_ai_config(self) -> dict:
        """获取当前AI配置"""
        return {
            'provider': self.AI_PROVIDER,
            'doubao': {
                'api_key': self.DOUBAO_API_KEY,
                'endpoint_id': self.DOUBAO_ENDPOINT_ID,
                'model': self.DOUBAO_MODEL,
                'timeout': self.DOUBAO_TIMEOUT
            },
            'deepseek': {
                'api_key': self.DEEPSEEK_API_KEY,
                'model': self.DEEPSEEK_MODEL,
                'timeout': self.DEEPSEEK_TIMEOUT
            },
            'qwen': {
                'api_key': self.QWEN_API_KEY,
                'model': self.QWEN_MODEL,
                'timeout': self.QWEN_TIMEOUT
            },
            'custom': {
                'api_key': self.CUSTOM_AI_API_KEY,
                'base_url': self.CUSTOM_AI_BASE_URL,
                'model': self.CUSTOM_AI_MODEL,
                'timeout': self.CUSTOM_AI_TIMEOUT
            }
        }

    def get_api_credentials(self) -> tuple:
        """获取API凭证"""
        return self.API_KEY, self.SECRET_KEY, self.PASSPHRASE

    @classmethod
    def from_env_file(cls, env_file: str = '.env'):
        """从指定的环境文件加载配置"""
        load_dotenv(env_file)
        return cls()


def reload_config():
    """重新加载全局配置"""
    global config
    load_dotenv('.env', override=True)
    config = Config()
    return config


# 全局配置实例
config = Config()
