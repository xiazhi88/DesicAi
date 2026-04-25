"""
AI对话记录管理器
自动保存AI分析对话到数据库和本地文件
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger


class ConversationLogger:
    """AI对话记录器"""

    def __init__(self, db_path: str = "data/trading_data.db", json_dir: str = "data/conversations"):
        """
        初始化对话记录器

        Args:
            db_path: 数据库路径
            json_dir: JSON文件保存目录
        """
        self.db_path = db_path
        self.json_dir = json_dir

        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(json_dir, exist_ok=True)

        self._init_database()

    @staticmethod
    def _format_beijing_time(utc_time_str: str) -> str:
        """
        将UTC时间字符串转换为北京时间格式

        Args:
            utc_time_str: UTC时间字符串 (YYYY-MM-DD HH:MM:SS)

        Returns:
            北京时间字符串 (YYYY-MM-DD HH:MM:SS)
        """
        try:
            # 解析UTC时间
            utc_dt = datetime.strptime(utc_time_str, '%Y-%m-%d %H:%M:%S')
            # 转换为北京时间 (UTC+8)
            beijing_dt = utc_dt + timedelta(hours=8)
            # 格式化为标准字符串
            return beijing_dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.warning(f"时间格式转换失败: {e}, 原始值: {utc_time_str}")
            return utc_time_str

    def _init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # AI对话记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                inst_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                response TEXT,
                features TEXT,
                analysis TEXT,
                signal TEXT,
                confidence INTEGER,
                is_executed BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 策略执行日志表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                inst_id TEXT NOT NULL,
                log_type TEXT NOT NULL,
                log_level TEXT NOT NULL,
                message TEXT NOT NULL,
                data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 创建索引（优化查询性能）
        # 1. conversations表：支持无session_id过滤时的时间排序查询
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_conversations_session ON ai_conversations(session_id, created_at DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON ai_conversations(created_at DESC)')

        # 2. strategy_logs表：支持无session_id过滤时的时间排序查询，以及log_type过滤
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_strategy_logs_session ON strategy_logs(session_id, created_at DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_strategy_logs_created_at ON strategy_logs(created_at DESC)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_strategy_logs_type_time ON strategy_logs(log_type, created_at DESC)')

        conn.commit()
        conn.close()

        logger.info("AI对话记录表初始化完成")

    def save_conversation(
        self,
        session_id: str,
        inst_id: str,
        prompt: str,
        response: Optional[str],
        features: Dict,
        analysis: Dict,
        is_executed: bool = False
    ) -> int:
        """
        保存AI对话记录

        Args:
            session_id: 会话ID
            inst_id: 交易对
            prompt: AI提示词
            response: AI响应
            features: 特征数据
            analysis: 分析结果
            is_executed: 是否已执行

        Returns:
            记录ID
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO ai_conversations
                (session_id, inst_id, prompt, response, features, analysis, signal, confidence, is_executed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                inst_id,
                prompt,
                response,
                json.dumps(features, ensure_ascii=False),
                json.dumps(analysis, ensure_ascii=False),
                analysis.get('signal', 'UNKNOWN'),
                analysis.get('confidence', 0),
                is_executed
            ))

            record_id = cursor.lastrowid
            conn.commit()

            # 同时保存到JSON文件
            self._save_to_json(session_id, {
                'id': record_id,
                'timestamp': datetime.now().isoformat(),
                'inst_id': inst_id,
                'prompt': prompt,
                'response': response,
                'features': features,
                'analysis': analysis,
                'is_executed': is_executed
            })

            logger.debug(f"保存AI对话记录: ID={record_id}")
            return record_id

        except Exception as e:
            logger.error(f"保存AI对话失败: {e}")
            return -1
        finally:
            conn.close()

    def _save_to_json(self, session_id: str, data: Dict):
        """保存到JSON文件（按日期分文件）"""
        try:
            date_str = datetime.now().strftime('%Y%m%d')
            json_file = os.path.join(self.json_dir, f"conversation_{date_str}.json")

            # 读取现有数据
            conversations = []
            if os.path.exists(json_file):
                with open(json_file, 'r', encoding='utf-8') as f:
                    conversations = json.load(f)

            # 追加新数据
            conversations.append(data)

            # 写入文件
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(conversations, f, ensure_ascii=False, indent=2)

        except Exception as e:
            #print(data)
            logger.error(f"保存JSON文件失败: {e}")

    def save_strategy_log(
        self,
        session_id: str,
        inst_id: str,
        log_type: str,
        log_level: str,
        message: str,
        data: Optional[Dict] = None
    ) -> int:
        """
        保存策略执行日志

        Args:
            session_id: 会话ID
            inst_id: 交易对
            log_type: 日志类型 (analysis/trade/risk/error)
            log_level: 日志级别 (info/warning/error/success)
            message: 日志消息
            data: 附加数据

        Returns:
            记录ID
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                INSERT INTO strategy_logs
                (session_id, inst_id, log_type, log_level, message, data)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                inst_id,
                log_type,
                log_level,
                message,
                json.dumps(data, ensure_ascii=False) if data else None
            ))

            record_id = cursor.lastrowid
            conn.commit()

            return record_id

        except Exception as e:
            logger.error(f"保存策略日志失败: {e}")
            return -1
        finally:
            conn.close()

    def get_recent_conversations(self, session_id: str = None, limit: int = 50) -> List[Dict]:
        """
        获取最近的AI对话记录

        Args:
            session_id: 会话ID（可选，不传则返回所有）
            limit: 返回数量

        Returns:
            对话记录列表
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            if session_id:
                cursor.execute('''
                    SELECT id, session_id, inst_id, prompt, response, signal,
                           confidence, is_executed, created_at
                    FROM ai_conversations
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ''', (session_id, limit))
            else:
                cursor.execute('''
                    SELECT id, session_id, inst_id, prompt, response, signal,
                           confidence, is_executed, created_at
                    FROM ai_conversations
                    ORDER BY created_at DESC
                    LIMIT ?
                ''', (limit,))

            rows = cursor.fetchall()

            conversations = []
            for row in rows:
                conversations.append({
                    'id': row[0],
                    'session_id': row[1],
                    'inst_id': row[2],
                    'prompt': row[3],  # 截断
                    'response': row[4],
                    'signal': row[5],
                    'confidence': row[6],
                    'is_executed': bool(row[7]),
                    'created_at': self._format_beijing_time(row[8])
                })

            return conversations

        except Exception as e:
            logger.error(f"查询AI对话记录失败: {e}")
            return []
        finally:
            conn.close()

    def get_recent_strategy_logs(self, session_id: str = None, limit: int = 100) -> List[Dict]:
        """
        获取最近的策略日志

        Args:
            session_id: 会话ID
            limit: 返回数量

        Returns:
            策略日志列表
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            if session_id:
                cursor.execute('''
                    SELECT id, inst_id, log_type, log_level, message, created_at
                    FROM strategy_logs
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ''', (session_id, limit))
            else:
                cursor.execute('''
                    SELECT id, inst_id, log_type, log_level, message, created_at
                    FROM strategy_logs
                    ORDER BY created_at DESC
                    LIMIT ?
                ''', (limit,))

            rows = cursor.fetchall()

            logs = []
            for row in rows:
                logs.append({
                    'id': row[0],
                    'inst_id': row[1],
                    'log_type': row[2],
                    'log_level': row[3],
                    'message': row[4],
                    'created_at': self._format_beijing_time(row[5])
                })

            return logs

        except Exception as e:
            logger.error(f"查询策略日志失败: {e}")
            return []
        finally:
            conn.close()

    def update_conversation_executed(self, conversation_id: int, is_executed: bool) -> bool:
        """
        更新会话的执行状态

        Args:
            conversation_id: 会话记录ID
            is_executed: 是否已执行（True=成功，False=失败）

        Returns:
            是否更新成功
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                UPDATE ai_conversations
                SET is_executed = ?
                WHERE id = ?
            ''', (is_executed, conversation_id))

            conn.commit()

            if cursor.rowcount > 0:
                logger.debug(f"更新会话执行状态: ID={conversation_id}, is_executed={is_executed}")
                return True
            else:
                logger.warning(f"未找到会话记录: ID={conversation_id}")
                return False

        except Exception as e:
            logger.error(f"更新会话执行状态失败: {e}")
            return False
        finally:
            conn.close()

    def get_conversation_detail(self, conversation_id: int) -> Optional[Dict]:
        """获取完整的对话详情"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT id, session_id, inst_id, prompt, response, features,
                       analysis, signal, confidence, is_executed, created_at
                FROM ai_conversations
                WHERE id = ?
            ''', (conversation_id,))

            row = cursor.fetchone()

            if row:
                return {
                    'id': row[0],
                    'session_id': row[1],
                    'inst_id': row[2],
                    'prompt': row[3],
                    'response': row[4],
                    'features': json.loads(row[5]) if row[5] else {},
                    'analysis': json.loads(row[6]) if row[6] else {},
                    'signal': row[7],
                    'confidence': row[8],
                    'is_executed': bool(row[9]),
                    'created_at': self._format_beijing_time(row[10])
                }

            return None

        except Exception as e:
            logger.error(f"查询对话详情失败: {e}")
            return None
        finally:
            conn.close()

    def export_session_to_json(self, session_id: str, output_file: str):
        """导出整个会话到JSON文件"""
        try:
            conversations = self.get_recent_conversations(session_id, limit=1000)

            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(conversations, f, ensure_ascii=False, indent=2)

            logger.success(f"导出会话成功: {output_file}")

        except Exception as e:
            logger.error(f"导出会话失败: {e}")


# 测试代码
if __name__ == '__main__':
    import uuid

    # 创建记录器
    logger_instance = ConversationLogger()

    # 测试保存对话
    session_id = str(uuid.uuid4())

    # 模拟AI对话
    features = {
        'kline': {'current_price': 50000, 'rsi': 65},
        'trades': {'pressure_ratio_5m': 1.2},
        'orderbook': {'spread_pct': 0.01}
    }

    analysis = {
        'signal': 'OPEN_LONG',
        'confidence': 75,
        'reason': '多数据源一致看涨'
    }

    conv_id = logger_instance.save_conversation(
        session_id=session_id,
        inst_id='BTC-USDT-SWAP',
        prompt='分析BTC当前走势',
        response='建议开多，置信度75%',
        features=features,
        analysis=analysis
    )

    print(f"保存对话记录: ID={conv_id}")

    # 测试保存策略日志
    log_id = logger_instance.save_strategy_log(
        session_id=session_id,
        inst_id='BTC-USDT-SWAP',
        log_type='trade',
        log_level='success',
        message='开仓成功',
        data={'order_id': '123456', 'price': 50000}
    )

    print(f"保存策略日志: ID={log_id}")

    # 查询记录
    conversations = logger_instance.get_recent_conversations(session_id, limit=10)
    print(f"查询到 {len(conversations)} 条对话记录")

    logs = logger_instance.get_recent_strategy_logs(session_id, limit=10)
    print(f"查询到 {len(logs)} 条策略日志")
