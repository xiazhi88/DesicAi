"""
交易对信息缓存管理
避免每次都调用API获取交易对信息
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger


class InstrumentCache:
    """交易对信息缓存管理类"""

    def __init__(self, cache_dir='data/cache', cache_hours=24):
        """
        初始化缓存管理器

        参数:
            cache_dir: 缓存目录
            cache_hours: 缓存有效期（小时），默认24小时
        """
        self.cache_dir = Path(cache_dir)
        self.cache_hours = cache_hours
        self.cache_file = self.cache_dir / 'instruments.json'

        # 确保缓存目录存在
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 内存缓存
        self._memory_cache = {}

    def get_instrument_info(self, inst_id, inst_type='SWAP', market_api=None, force_refresh=False):
        """
        获取交易对信息（优先从缓存读取）

        参数:
            inst_id: 交易对ID，如 'BTC-USDT-SWAP'
            inst_type: 产品类型，默认 'SWAP'
            market_api: MarketAPI实例（需要刷新时使用）
            force_refresh: 是否强制刷新缓存

        返回:
            {
                'success': True/False,
                'data': {...},  # 交易对信息
                'from_cache': True/False,  # 是否来自缓存
                'error': '...'  # 错误信息
            }
        """
        # 1. 检查内存缓存
        if not force_refresh and inst_id in self._memory_cache:
            cache_data = self._memory_cache[inst_id]
            if self._is_cache_valid(cache_data):
                logger.debug(f"从内存缓存读取 {inst_id}")
                return {
                    'success': True,
                    'data': cache_data['data'],
                    'from_cache': True
                }

        # 2. 检查文件缓存
        if not force_refresh:
            file_cache = self._load_from_file()
            if inst_id in file_cache and self._is_cache_valid(file_cache[inst_id]):
                logger.debug(f"从文件缓存读取 {inst_id}")
                cache_data = file_cache[inst_id]
                # 更新到内存缓存
                self._memory_cache[inst_id] = cache_data
                return {
                    'success': True,
                    'data': cache_data['data'],
                    'from_cache': True
                }

        # 3. 缓存未命中或过期，从API获取
        if market_api is None:
            return {
                'success': False,
                'error': '缓存未命中且未提供 market_api'
            }

        logger.info(f"从API获取 {inst_id} 信息")
        result = market_api.get_instruments(inst_type=inst_type, inst_id=inst_id)

        if result.get('code') != '0':
            return {
                'success': False,
                'error': f"API调用失败: {result.get('msg')}"
            }

        inst_data = result.get('data', [])
        if not inst_data:
            return {
                'success': False,
                'error': f'未找到交易对 {inst_id}'
            }

        inst_info = inst_data[0]

        # 4. 保存到缓存
        cache_data = {
            'data': inst_info,
            'timestamp': datetime.now().isoformat(),
            'inst_id': inst_id,
            'inst_type': inst_type
        }

        # 保存到内存缓存
        self._memory_cache[inst_id] = cache_data

        # 保存到文件缓存
        self._save_to_file(inst_id, cache_data)

        return {
            'success': True,
            'data': inst_info,
            'from_cache': False
        }

    def _is_cache_valid(self, cache_data):
        """检查缓存是否有效"""
        try:
            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            expire_time = cache_time + timedelta(hours=self.cache_hours)
            return datetime.now() < expire_time
        except Exception as e:
            logger.warning(f"检查缓存有效性失败: {e}")
            return False

    def _load_from_file(self):
        """从文件加载缓存"""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"加载缓存文件失败: {e}")
        return {}

    def _save_to_file(self, inst_id, cache_data):
        """保存到文件"""
        try:
            # 读取现有缓存
            all_cache = self._load_from_file()

            # 更新
            all_cache[inst_id] = cache_data

            # 写入文件
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(all_cache, f, indent=2, ensure_ascii=False)

            logger.debug(f"已保存 {inst_id} 到缓存文件")
        except Exception as e:
            logger.warning(f"保存缓存文件失败: {e}")

    def clear_cache(self, inst_id=None):
        """
        清除缓存

        参数:
            inst_id: 指定交易对ID，None表示清除所有
        """
        if inst_id is None:
            # 清除所有缓存
            self._memory_cache.clear()
            if self.cache_file.exists():
                self.cache_file.unlink()
            logger.info("已清除所有缓存")
        else:
            # 清除指定交易对缓存
            if inst_id in self._memory_cache:
                del self._memory_cache[inst_id]

            all_cache = self._load_from_file()
            if inst_id in all_cache:
                del all_cache[inst_id]
                with open(self.cache_file, 'w', encoding='utf-8') as f:
                    json.dump(all_cache, f, indent=2, ensure_ascii=False)

            logger.info(f"已清除 {inst_id} 缓存")

    def get_cache_info(self):
        """获取缓存信息"""
        file_cache = self._load_from_file()

        info = {
            'memory_cache_count': len(self._memory_cache),
            'file_cache_count': len(file_cache),
            'cache_file': str(self.cache_file),
            'cache_hours': self.cache_hours,
            'instruments': []
        }

        for inst_id, cache_data in file_cache.items():
            is_valid = self._is_cache_valid(cache_data)
            cache_time = datetime.fromisoformat(cache_data['timestamp'])

            info['instruments'].append({
                'inst_id': inst_id,
                'cached_at': cache_data['timestamp'],
                'is_valid': is_valid,
                'age_hours': (datetime.now() - cache_time).total_seconds() / 3600
            })

        return info

    def refresh_all(self, market_api, inst_type='SWAP'):
        """
        刷新所有缓存的交易对信息

        参数:
            market_api: MarketAPI实例
            inst_type: 产品类型
        """
        file_cache = self._load_from_file()
        refreshed = []
        failed = []

        for inst_id in file_cache.keys():
            logger.info(f"刷新 {inst_id}...")
            result = self.get_instrument_info(
                inst_id=inst_id,
                inst_type=inst_type,
                market_api=market_api,
                force_refresh=True
            )

            if result['success']:
                refreshed.append(inst_id)
            else:
                failed.append(inst_id)

        logger.info(f"刷新完成: 成功 {len(refreshed)}, 失败 {len(failed)}")
        return {
            'refreshed': refreshed,
            'failed': failed
        }


# 全局缓存实例
_global_cache = None


def get_cache(cache_dir='data/cache', cache_hours=24):
    """获取全局缓存实例"""
    global _global_cache
    if _global_cache is None:
        _global_cache = InstrumentCache(cache_dir=cache_dir, cache_hours=cache_hours)
    return _global_cache
