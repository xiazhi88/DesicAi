"""
AI模型Token使用统计跟踪器
记录每次API调用的token消耗和费用
"""
import sqlite3
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from loguru import logger

def find_project_root():
    """
    查找实际的项目根目录，处理开发环境和wheel安装两种场景

    当通过wheel安装时，代码位于site-packages，但项目文件(.env, data/, logs/)
    位于用户的实际项目目录。此函数通过查找标记文件来定位正确的项目根目录。
    """
    # 优先使用当前工作目录（用户运行应用的位置）
    cwd = os.getcwd()
    if os.path.exists(os.path.join(cwd, '.env')) or \
       os.path.exists(os.path.join(cwd, 'data')):
        return cwd

    # 向上搜索父目录（最多5层）
    current = cwd
    for _ in range(5):
        if os.path.exists(os.path.join(current, '.env')) or \
           os.path.exists(os.path.join(current, 'data')) or \
           os.path.exists(os.path.join(current, 'logs')):
            return current
        parent = os.path.dirname(current)
        if parent == current:  # 已到达根目录
            break
        current = parent

    # 如果都没找到，使用当前工作目录作为后备
    return cwd

project_root = find_project_root()
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TokenUsageTracker:
    """Token使用统计跟踪器"""

    # 豆包模型定价（每百万token的价格，单位：元）
    PRICING = {
        "doubao-pro-32k": {
            "input": 0.8,   # 输入token价格/百万
            "output": 2.0,  # 输出token价格/百万
            "cache_read": 0.1,  # 缓存读取价格/百万
            "cache_creation": 1.25  # 缓存创建价格/百万
        },
        "doubao-lite-32k": {
            "input": 0.3,
            "output": 0.6,
            "cache_read": 0.05,
            "cache_creation": 0.375
        }
    }

    def __init__(self, db_path: str = None):
        """
        初始化Token使用跟踪器

        Args:
            db_path: 数据库路径
        """
        if db_path is None:
            db_path = os.path.join(project_root, "data", "trading_data.db")

        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT NOT NULL,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                cached_tokens INTEGER DEFAULT 0,
                input_cost REAL DEFAULT 0,
                output_cost REAL DEFAULT 0,
                cache_cost REAL DEFAULT 0,
                total_cost REAL DEFAULT 0,
                session_id TEXT,
                endpoint TEXT,
                success BOOLEAN DEFAULT 1,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 创建索引以提高查询性能
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_token_usage_created_at
            ON ai_token_usage(created_at)
        ''')

        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_token_usage_model
            ON ai_token_usage(model_name)
        ''')

        conn.commit()
        conn.close()
        logger.debug("Token使用统计表已初始化")

    def record_usage(
        self,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cached_tokens: int = 0,
        session_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        success: bool = True,
        error_message: Optional[str] = None
    ):
        """
        记录一次API调用的token使用情况

        Args:
            model_name: 模型名称
            prompt_tokens: 输入token数
            completion_tokens: 输出token数
            total_tokens: 总token数
            cached_tokens: 缓存token数
            session_id: 会话ID
            endpoint: 调用端点
            success: 是否成功
            error_message: 错误信息
        """
        try:
            # 计算费用
            costs = self._calculate_cost(
                model_name,
                prompt_tokens,
                completion_tokens,
                cached_tokens
            )

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute('''
                INSERT INTO ai_token_usage (
                    model_name, prompt_tokens, completion_tokens, total_tokens,
                    cached_tokens, input_cost, output_cost, cache_cost, total_cost,
                    session_id, endpoint, success, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                model_name, prompt_tokens, completion_tokens, total_tokens,
                cached_tokens, costs['input'], costs['output'], costs['cache'], costs['total'],
                session_id, endpoint, success, error_message
            ))

            conn.commit()
            conn.close()

            logger.debug(
                f"记录Token使用: {model_name} | "
                f"输入={prompt_tokens} 输出={completion_tokens} | "
                f"费用={costs['total']:.6f}元"
            )

        except Exception as e:
            logger.error(f"记录Token使用失败: {e}")

    def _calculate_cost(
        self,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0
    ) -> Dict[str, float]:
        """
        计算费用

        Args:
            model_name: 模型名称
            prompt_tokens: 输入token数
            completion_tokens: 输出token数
            cached_tokens: 缓存token数

        Returns:
            费用字典 {input, output, cache, total}
        """
        # 查找模型定价
        pricing = None
        for key in self.PRICING.keys():
            if key in model_name.lower():
                pricing = self.PRICING[key]
                break

        if not pricing:
            # 默认使用pro版定价
            pricing = self.PRICING["doubao-pro-32k"]
            logger.warning(f"未找到模型 {model_name} 的定价，使用默认定价")

        # 计算费用（价格是每百万token）
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        cache_cost = (cached_tokens / 1_000_000) * pricing.get("cache_read", 0)

        total_cost = input_cost + output_cost + cache_cost

        return {
            'input': input_cost,
            'output': output_cost,
            'cache': cache_cost,
            'total': total_cost
        }

    def get_today_usage(self) -> Dict:
        """
        获取今日Token使用统计

        Returns:
            统计数据字典
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            # 获取今日统计
            cursor.execute('''
                SELECT
                    COUNT(*) as api_calls,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(cached_tokens) as total_cached_tokens,
                    SUM(input_cost) as total_input_cost,
                    SUM(output_cost) as total_output_cost,
                    SUM(cache_cost) as total_cache_cost,
                    SUM(total_cost) as total_cost,
                    COUNT(CASE WHEN success = 1 THEN 1 END) as success_count,
                    COUNT(CASE WHEN success = 0 THEN 1 END) as error_count
                FROM ai_token_usage
                WHERE date(created_at) = date('now', 'localtime')
            ''')

            row = cursor.fetchone()

            # 按模型分组统计
            cursor.execute('''
                SELECT
                    model_name,
                    COUNT(*) as calls,
                    SUM(prompt_tokens) as prompt_tokens,
                    SUM(completion_tokens) as completion_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(total_cost) as cost
                FROM ai_token_usage
                WHERE date(created_at) = date('now', 'localtime')
                GROUP BY model_name
            ''')

            models = []
            for model_row in cursor.fetchall():
                models.append({
                    'model_name': model_row['model_name'],
                    'calls': model_row['calls'],
                    'prompt_tokens': model_row['prompt_tokens'] or 0,
                    'completion_tokens': model_row['completion_tokens'] or 0,
                    'total_tokens': model_row['total_tokens'] or 0,
                    'cost': round(model_row['cost'] or 0, 6)
                })

            result = {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'api_calls': row['api_calls'] or 0,
                'total_prompt_tokens': row['total_prompt_tokens'] or 0,
                'total_completion_tokens': row['total_completion_tokens'] or 0,
                'total_tokens': row['total_tokens'] or 0,
                'total_cached_tokens': row['total_cached_tokens'] or 0,
                'total_input_cost': round(row['total_input_cost'] or 0, 6),
                'total_output_cost': round(row['total_output_cost'] or 0, 6),
                'total_cache_cost': round(row['total_cache_cost'] or 0, 6),
                'total_cost': round(row['total_cost'] or 0, 6),
                'success_count': row['success_count'] or 0,
                'error_count': row['error_count'] or 0,
                'models': models
            }

            return result

        except Exception as e:
            logger.error(f"获取今日使用统计失败: {e}")
            return {}
        finally:
            conn.close()

    def get_usage_history(self, days: int = 7) -> List[Dict]:
        """
        获取最近N天的使用统计

        Args:
            days: 天数

        Returns:
            每日统计列表
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT
                    date(created_at, 'localtime') as date,
                    COUNT(*) as api_calls,
                    SUM(total_tokens) as total_tokens,
                    SUM(total_cost) as total_cost,
                    COUNT(CASE WHEN success = 1 THEN 1 END) as success_count
                FROM ai_token_usage
                WHERE created_at >= datetime('now', 'localtime', '-' || ? || ' days')
                GROUP BY date(created_at, 'localtime')
                ORDER BY date DESC
            ''', (days,))

            history = []
            for row in cursor.fetchall():
                history.append({
                    'date': row['date'],
                    'api_calls': row['api_calls'],
                    'total_tokens': row['total_tokens'] or 0,
                    'total_cost': round(row['total_cost'] or 0, 6),
                    'success_count': row['success_count']
                })

            return history

        except Exception as e:
            logger.error(f"获取历史统计失败: {e}")
            return []
        finally:
            conn.close()

    def get_recent_calls(self, limit: int = 20) -> List[Dict]:
        """
        获取最近的API调用记录

        Args:
            limit: 返回记录数量

        Returns:
            调用记录列表
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            cursor.execute('''
                SELECT
                    model_name, prompt_tokens, completion_tokens, total_tokens,
                    total_cost, session_id, endpoint, success, error_message,
                    created_at
                FROM ai_token_usage
                ORDER BY created_at DESC
                LIMIT ?
            ''', (limit,))

            calls = []
            for row in cursor.fetchall():
                calls.append({
                    'model_name': row['model_name'],
                    'prompt_tokens': row['prompt_tokens'],
                    'completion_tokens': row['completion_tokens'],
                    'total_tokens': row['total_tokens'],
                    'total_cost': round(row['total_cost'], 6),
                    'session_id': row['session_id'],
                    'endpoint': row['endpoint'],
                    'success': bool(row['success']),
                    'error_message': row['error_message'],
                    'created_at': row['created_at']
                })

            return calls

        except Exception as e:
            logger.error(f"获取最近调用记录失败: {e}")
            return []
        finally:
            conn.close()


# 全局单例
_tracker_instance = None


def get_tracker() -> TokenUsageTracker:
    """获取全局TokenUsageTracker实例"""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = TokenUsageTracker()
    return _tracker_instance


if __name__ == '__main__':
    # 测试
    tracker = TokenUsageTracker()

    # 模拟记录
    tracker.record_usage(
        model_name="doubao-pro-32k",
        prompt_tokens=1500,
        completion_tokens=800,
        total_tokens=2300,
        session_id="test-session-001"
    )

    # 查询今日统计
    today_stats = tracker.get_today_usage()
    print("今日统计:", today_stats)

    # 查询历史
    history = tracker.get_usage_history(days=7)
    print("\n最近7天:")
    for day in history:
        print(f"  {day['date']}: {day['api_calls']}次调用, {day['total_tokens']}tokens, ¥{day['total_cost']}")
