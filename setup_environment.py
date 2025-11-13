#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动化环境配置脚本
支持 Windows 和 Ubuntu 系统
功能：
1. 检测并创建 Python venv 虚拟环境
2. 检测并安装 MySQL，创建 trading_data 数据库
3. 检测并安装 Redis
"""

import os
import sys
import subprocess
import platform
import shutil
import glob
from pathlib import Path


class EnvironmentSetup:
    def __init__(self):
        self.system = platform.system().lower()
        self.is_windows = self.system == "windows"
        self.is_linux = self.system == "linux"
        self.venv_path = Path("venv")
        self.mysql_password = "123456789"
        self.db_name = "trading_data"
        self.env_example_path = Path(".env.example")
        self.env_path = Path(".env")
        self.wheelhouse_path = Path("wheelhouse")
        self.data_path = Path("data")
        self.prompts_json_path = self.data_path / "prompts.json"

    def get_python_version_tag(self):
        """获取 Python 版本标签 (cp37, cp38, cp39, cp310, cp311, cp312, cp313)"""
        version_info = sys.version_info
        return f"cp{version_info.major}{version_info.minor}"

    def get_platform_tag(self):
        """获取平台标签 (win_amd64, manylinux_x86_64, macosx_arm64, etc.)"""
        system = platform.system().lower()
        machine = platform.machine().lower()

        # 标准化架构名称
        if machine in ('x86_64', 'amd64', 'x64'):
            arch = 'x86_64'
        elif machine in ('aarch64', 'arm64'):
            arch = 'arm64'
        else:
            arch = machine

        if system == 'windows':
            if arch == 'x86_64':
                return 'win_amd64'
            elif arch == 'arm64':
                return 'win_arm64'
            else:
                return f'win_{arch}'
        elif system == 'linux':
            # Linux 通常使用 manylinux 标签
            if arch == 'x86_64':
                # 匹配 manylinux2014_x86_64 或类似格式
                return 'manylinux*_x86_64*'
            elif arch == 'arm64':
                return 'manylinux*_aarch64*'
            else:
                return f'linux_{arch}'
        elif system == 'darwin':
            # macOS
            if arch == 'x86_64':
                return 'macosx*_x86_64'
            elif arch == 'arm64':
                return 'macosx*_arm64'
            else:
                return f'macosx*_{arch}'
        else:
            return None

    def find_matching_wheel(self):
        """查找匹配当前系统的 wheel 包"""
        if not self.wheelhouse_path.exists():
            return None

        py_tag = self.get_python_version_tag()
        platform_tag = self.get_platform_tag()

        if not platform_tag:
            return None

        # 构建搜索模式 (支持新旧包名)
        # 新格式: DesicAi_okx-0.1.0-cp311-cp311-win_amd64.whl
        # 旧格式: aiquant_trade-0.1.0-cp311-cp311-win_amd64.whl
        patterns = [
            f"DesicAi_okx-*-{py_tag}-{py_tag}-{platform_tag}.whl",
            f"aiquant_trade-*-{py_tag}-{py_tag}-{platform_tag}.whl"  # 兼容旧版本
        ]

        matching_files = []
        for pattern in patterns:
            search_path = self.wheelhouse_path / pattern
            matching_files.extend(glob.glob(str(search_path)))

        if matching_files:
            # 返回第一个匹配的文件（优先新包名）
            return Path(matching_files[0])

        # 如果没找到，尝试模糊匹配（处理 manylinux/macosx 等复杂标签）
        if not matching_files:
            # 尝试更宽松的匹配
            all_wheels = (
                list(self.wheelhouse_path.glob(f"DesicAi_okx-*-{py_tag}-*.whl")) +
                list(self.wheelhouse_path.glob(f"aiquant_trade-*-{py_tag}-*.whl"))
            )

            # 过滤出匹配当前平台的
            for wheel in all_wheels:
                wheel_name = wheel.name.lower()

                if self.is_windows and 'win' in wheel_name:
                    if 'amd64' in wheel_name or 'win_amd64' in wheel_name:
                        return wheel
                elif self.is_linux and ('linux' in wheel_name or 'manylinux' in wheel_name):
                    if 'x86_64' in wheel_name or 'aarch64' in wheel_name:
                        return wheel
                elif platform.system().lower() == 'darwin' and ('macosx' in wheel_name):
                    if 'x86_64' in wheel_name or 'arm64' in wheel_name:
                        return wheel

        return None

    def install_trade_wheel(self):
        """自动检测并安装对应的 trade.py wheel 包"""
        self.print_header("检测并安装 Trade 加密模块")

        # 检查 wheelhouse 目录是否存在
        if not self.wheelhouse_path.exists():
            print(f"✗ wheelhouse 目录不存在: {self.wheelhouse_path.absolute()}")
            print("  请先运行构建命令生成 wheel 包:")
            print("    python setup.py bdist_wheel  # 当前系统")
            print("    或")
            print("    python -m cibuildwheel --output-dir wheelhouse  # 多版本")
            print("  跳过 Trade 模块安装")
            return False

        # 列出所有可用的 wheel (支持两种包名)
        all_wheels = (
            list(self.wheelhouse_path.glob("DesicAi_okx-*.whl")) +
            list(self.wheelhouse_path.glob("aiquant_trade-*.whl"))  # 兼容旧版本
        )
        if all_wheels:
            print(f"\n找到 {len(all_wheels)} 个 wheel 包:")
            for w in all_wheels[:5]:  # 只显示前5个
                print(f"  • {w.name}")
            if len(all_wheels) > 5:
                print(f"  ... 还有 {len(all_wheels) - 5} 个")

        # 查找匹配的 wheel 包
        print(f"\n正在查找匹配当前系统的 wheel 包...")
        print(f"  Python: {sys.version_info.major}.{sys.version_info.minor} ({self.get_python_version_tag()})")
        print(f"  系统: {platform.system()}")
        print(f"  架构: {platform.machine()}")
        print(f"  平台标签: {self.get_platform_tag()}")

        wheel_file = self.find_matching_wheel()

        if not wheel_file:
            print(f"\n✗ 未找到匹配的 wheel 包")
            print(f"  需要的配置:")
            print(f"    - Python: {sys.version_info.major}.{sys.version_info.minor}")
            print(f"    - 系统: {platform.system()}")
            print(f"    - 架构: {platform.machine()}")
            print(f"\n  解决方案:")
            print(f"    1. 使用 GitHub Actions 构建所有平台的 wheel")
            print(f"    2. 或在当前系统构建: python setup.py bdist_wheel")
            print(f"    3. 将生成的 wheel 移动到 wheelhouse/ 目录")
            return False

        print(f"\n✓ 找到匹配的 wheel 包: {wheel_file.name}")

        # 安装 wheel 包
        try:
            print(f"\n正在安装 wheel 包...")

            # 确定 pip 命令
            if self.venv_path.exists():
                # 如果虚拟环境存在，使用虚拟环境的 pip
                if self.is_windows:
                    pip_cmd = str(self.venv_path / "Scripts" / "pip.exe")
                else:
                    pip_cmd = str(self.venv_path / "bin" / "pip")

                # 检查 pip 是否存在
                if not Path(pip_cmd).exists():
                    print(f"  警告: 虚拟环境 pip 不存在，使用系统 pip")
                    pip_cmd = f"{sys.executable} -m pip"
            else:
                # 否则使用系统 pip
                pip_cmd = f"{sys.executable} -m pip"

            print(f"  使用 pip: {pip_cmd}")

            # 执行安装命令（先安装依赖）
            print(f"  安装依赖...")
            deps_cmd = f'{pip_cmd} install -q requests pandas numpy loguru'
            self.run_command(deps_cmd, check=False, capture_output=True)

            # 安装 wheel
            print(f"  安装 DesicAi-okx...")
            install_cmd = f'{pip_cmd} install --force-reinstall "{wheel_file.absolute()}"'
            result = self.run_command(install_cmd, check=False, capture_output=True)

            if result and result.returncode == 0:
                print(f"\n✓ Trade 加密模块安装成功!")

                # 验证安装
                print(f"\n验证安装...")
                verify_cmd = f'{sys.executable} -c "from src.api.trade import TradeAPI; print(\\\"✓ 导入成功\\\")"'
                verify_result = self.run_command(verify_cmd, check=False, capture_output=True)

                if verify_result and verify_result.returncode == 0:
                    print(verify_result.stdout.strip())
                    print(f"✓ Trade 模块验证通过!")
                else:
                    print(f"  警告: 导入测试失败，但模块已安装")

                return True
            else:
                print(f"\n✗ Trade 模块安装失败")
                if result and result.stderr:
                    print(f"  错误信息:")
                    for line in result.stderr.split('\n')[:10]:  # 只显示前10行错误
                        if line.strip():
                            print(f"    {line}")
                return False

        except Exception as e:
            print(f"\n✗ Trade 模块安装失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def print_header(self, text):
        """打印标题"""
        print("\n" + "="*60)
        print(f"  {text}")
        print("="*60)

    def run_command(self, cmd, shell=True, check=False, capture_output=False):
        """执行系统命令"""
        try:
            if capture_output:
                result = subprocess.run(cmd, shell=shell, check=check,
                                      capture_output=True, text=True)
                return result
            else:
                result = subprocess.run(cmd, shell=shell, check=check)
                return result
        except subprocess.CalledProcessError as e:
            print(f"命令执行失败: {e}")
            return None

    def check_and_create_data_directory(self):
        """检测并创建 data 目录和 prompts.json 文件"""
        self.print_header("检测 data 目录和配置文件")

        # 检查并创建 data 目录
        if not self.data_path.exists():
            try:
                print(f"正在创建 data 目录: {self.data_path.absolute()}")
                self.data_path.mkdir(parents=True, exist_ok=True)
                print(f"✓ data 目录创建成功")
            except Exception as e:
                print(f"✗ data 目录创建失败: {e}")
                return False
        else:
            print(f"✓ data 目录已存在: {self.data_path.absolute()}")

        # 检查并创建 prompts.json 文件
        if self.prompts_json_path.exists():
            print(f"✓ prompts.json 文件已存在: {self.prompts_json_path.absolute()}")
            return True

        try:
            print(f"正在创建 prompts.json 文件...")

            # 默认的 prompts 内容
            default_prompts = [
                {
                    "name": "默认Prompt",
                    "description": "",
                    "content": """### 1. 核心决策原则（交易哲学）\n\n\n\n* **概率思维**：不追求 "必胜" 交易，承认市场无确定性、仅有概率分布。所有决策均基于风险回报比与成功概率评估，坦然接受合理亏损（交易的必然组成部分）。\n\n* **风险第一**：任何分析前先明确单笔交易的最大潜在损失，严格遵守 "单次亏损不超过预设总资本风险限额" 规则，保护本金为首要职责。\n\n* **边缘捕捉**：聚焦市场微观 / 宏观结构的非均衡状态（源于情绪过度反应、流动性暂时失衡、宏观逻辑驱动的长期趋势等），综合基本面、技率 × 潜在盈利) - (下跌概率 × 潜在亏损)`，做空逻辑相反），仅参与预期价值为正的机会，并持续优化公式变量。\n\n* **复盘优化**：定期回顾过往决策，总结经验教训，修正错误交易逻辑。\n\n### 2. 数据处理与情境感知\n\n将 K 线、Tick 聚合数据等接收信息视为市场深层结构的表层现象，核心任务包括：\n\n\n\n* **解读叙事**：厘清当前市场的主导逻辑与核心驱动因素。\n\n* **识别周期**：判断市场处于趋势、震荡、反转或混乱阶段。\n\n* **感知情绪**：从价格波动、成交量变化中捕捉市场参与者的贪婪与恐惧程度。\n\n* **寻找共鸣 / 背离**：验证不同数据维度的一致性（共鸣）或矛盾性（背离），例如价格创新高但动能指标减弱的背离信号需重点 关注。\n\n### 3. 自主决策框架\n\n基于上述原则与感知，每笔交易决策需提供清晰逻辑链：\n\n\n\n* **【机会阐述】**：详细说明 交易的核心逻辑（如驱动因素、结构特征、机会本质等）。\n\n* **【概率与赔率评估】**：\n\n\n  * 入场合理性：明确当前价位与时机的核心优势（如支撑 / 阻力位、逻辑拐点等）。\n\n  * 成功概率：给出主观概率评估（示例：55%），需基于客观信息推导。\n\n  * 风险回报比：预估潜在盈利与潜在亏损的比例（示例：1:3 以上）。\n\n* **【具体操作建议】**：\n\n\n  * 方向：明确做多 / 做 空 \\[具体交易标的]。\n\n  * 止损：设置具体价位（示例：XX 元），该价位需对应 "核心交易逻辑失效" 的判断标准。\n\n  * 止盈 / 目标：设置具体价位（示例：XX 元），该价位需契合技术目标区、逻辑兑现点或概率优势耗尽的节点。\n\n  * 补充规则：开仓时设定的止盈止损需基于市场分析，无重大特殊情况不得频繁调整；若需调整，必须基于新的市场分析预判，避免因短期波动情绪化操作。\n\n* **【后续预案】**：\n\n\n  * 盈利方向移动：说明如何调整止损以保护已有利润（如移动止损、跟踪止损等具体方式）。\n\n  * 新信息出现：明确调整目标的触发条件（如宏观数据发布、技术形态破位等）。\n\n### 4. 行为禁令（不可逾越规则）\n\n\n\n* 禁止 报复性交易：亏损后不得为 "回本" 进行情绪化、超出常规风险限额的交易。\n\n* 禁止违背止损：止损位触及后必须无条件执行，不得以 "再等等" 为由随意移动止损。\n\n### 5. 整体账户管理\n\n\n\n* 历史持仓：客观分析过往多空交易绩效，识别方向性偏见、风险 控制漏洞等模式。\n\n* 历史决策：复盘过去决策逻辑，判断当初判断是否合理，是否需基于当前认知修正。\n\n* 历史收益：评估收益是否达成目标，分析亏损原因（判断失误 / 执行偏差）、盈利是否未达预期（提前离场 / 目标设置不合理），明确改进空间。\n\n### 6. 持仓条件（持续持有需满足）\n\n\n\n* 多空视角下的核心交易逻辑仍有效。\n\n* 市场未出现损害当前头寸方向的重大变化（如核 心驱动因素消失、基本面反转等）。\n\n* 重新评估多空风险后，仍支持当前持仓方向。\n\n### 7. 思考模型：强制应用框架\n\n每次 响应前，必须按以下顺序完成分析步骤：\n\n\n\n1. 独立思考：拒绝盲从市场共识，仅基于实时数据与核心逻辑形成独立观点。\n\n2. 多维度验证：动态权衡以下因素的重要性（无需平等对待）：\n\n* 价格行为与技术信号（支撑 / 阻力、动量指标、形态结构等）；\n\n* 市场微观结构（流动性分布、订单簿深度、买卖盘力量等）；\n\n* 多空力量对比（资金费率、未平仓合约变化、大额成交方向等） ；\n\n* 系统风险（波动率突变、资产相关性破裂、黑天鹅事件等）。\n\n1. 证伪思维：主动寻找推翻当前判断的反证（如看多则排查 隐藏看空信号，看空则排查隐藏看多信号）。\n\n2. 概率思维：所有决策需基于 0-100 的置信度评估，无确定性机会；多空置信度需依托客观证据权重，避免方向性偏见。\n\n3. 持续学习：参考历史决策、市场变化及持仓复盘结论，但不过度依赖过往模式（需结合实时 市场动态调整）。\n\n4. 避免过度调整：拒绝频繁变动仓位或交易参数，耐心等待市场逻辑兑现；若当前无持仓，基于新分析独立决策 ；无需调整时明确给出 "HOLD" 信号。\n\n5. 开仓方向：支持做多与做空，两者适用完全一致的风险管理标准。\n\n'""",
                    "created_at": "2025-11-09 15:20:27",
                    "updated_at": "2025-11-09 16:49:28"
                }
            ]

            # 写入 JSON 文件
            import json
            with open(self.prompts_json_path, 'w', encoding='utf-8') as f:
                json.dump(default_prompts, f, ensure_ascii=False, indent=2)

            print(f"✓ prompts.json 文件创建成功: {self.prompts_json_path.absolute()}")
            return True

        except Exception as e:
            print(f"✗ prompts.json 文件创建失败: {e}")
            return False

    def check_and_create_env_file(self):
        """检测并创建 .env 配置文件"""
        self.print_header("检测 .env 配置文件")

        # 检查 .env 是否已存在
        if self.env_path.exists():
            print(f"✓ .env 文件已存在: {self.env_path.absolute()}")
            return True

        # 检查 .env.example 是否存在
        if not self.env_example_path.exists():
            print(f"✗ .env.example 文件不存在，跳过 .env 创建")
            return False

        # 复制 .env.example 到 .env
        try:
            print(f"正在创建 .env 文件从 {self.env_example_path}")

            if self.is_windows:
                # Windows 使用 copy 命令
                cmd = f'copy "{self.env_example_path}" "{self.env_path}"'
            else:
                # Linux/Unix 使用 cp 命令
                cmd = f'cp "{self.env_example_path}" "{self.env_path}"'

            result = self.run_command(cmd, check=False)

            if result and result.returncode == 0:
                print(f"✓ .env 文件创建成功: {self.env_path.absolute()}")
                return True
            else:
                # 如果命令失败，使用 Python 的 shutil 作为备选方案
                print("尝试使用 Python 方式复制文件...")
                shutil.copy2(self.env_example_path, self.env_path)
                print(f"✓ .env 文件创建成功: {self.env_path.absolute()}")
                return True

        except Exception as e:
            print(f"✗ .env 文件创建失败: {e}")
            return False

    def check_venv(self):
        """检测 venv 环境"""
        self.print_header("检测 Python 虚拟环境")

        if self.venv_path.exists():
            print(f"✓ 虚拟环境已存在: {self.venv_path.absolute()}")
            return True
        else:
            print(f"✗ 虚拟环境不存在")
            return False

    def create_venv(self):
        """创建 venv 虚拟环境"""
        self.print_header("创建 Python 虚拟环境")

        try:
            print(f"正在创建虚拟环境: {self.venv_path.absolute()}")
            subprocess.run([sys.executable, "-m", "venv", str(self.venv_path)], check=True)
            print(f"✓ 虚拟环境创建成功!")

            # 提示激活命令
            if self.is_windows:
                activate_cmd = f"{self.venv_path}\\Scripts\\activate"
                print(f"\n激活虚拟环境命令: {activate_cmd}")
            else:
                activate_cmd = f"source {self.venv_path}/bin/activate"
                print(f"\n激活虚拟环境命令: {activate_cmd}")

            return True
        except Exception as e:
            print(f"✗ 创建虚拟环境失败: {e}")
            return False

    def get_mysql_password_from_user(self):
        """让用户输入 MySQL 密码"""
        import getpass
        try:
            print("\n请输入 MySQL root 密码 (输入不会显示):")
            password = getpass.getpass("密码: ")
            return password
        except Exception:
            # 如果 getpass 失败，使用普通 input
            password = input("密码: ")
            return password

    def test_mysql_connection(self, password):
        """测试 MySQL 连接"""
        cmd = f'mysql -u root -p{password} -e "SELECT 1;" 2>&1'
        result = self.run_command(cmd, check=False, capture_output=True)

        if result and result.returncode == 0:
            return True

        # Linux 尝试 sudo
        if self.is_linux:
            cmd = 'sudo mysql -e "SELECT 1;" 2>&1'
            result = self.run_command(cmd, check=False, capture_output=True)
            if result and result.returncode == 0:
                return True

        return False

    def get_mysql_port(self, password):
        """获取 MySQL 端口号"""
        try:
            # 尝试使用密码连接
            cmd = f'mysql -u root -p{password} -e "SHOW VARIABLES LIKE \'port\';"'
            result = self.run_command(cmd, check=False, capture_output=True)

            if result and result.returncode == 0:
                # 解析输出获取端口号
                for line in result.stdout.split('\n'):
                    if 'port' in line.lower() and not line.startswith('Variable'):
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[-1]
            else:
                # 尝试使用 sudo（Linux）
                if self.is_linux:
                    cmd = 'sudo mysql -e "SHOW VARIABLES LIKE \'port\';"'
                    result = self.run_command(cmd, check=False, capture_output=True)
                    if result and result.returncode == 0:
                        for line in result.stdout.split('\n'):
                            if 'port' in line.lower() and not line.startswith('Variable'):
                                parts = line.split()
                                if len(parts) >= 2:
                                    return parts[-1]

            # 默认端口
            return "3306"
        except Exception:
            return "3306"

    def check_mysql(self):
        """检测 MySQL 是否安装"""
        self.print_header("检测 MySQL")

        if self.is_windows:
            cmd = "mysql --version"
        else:
            cmd = "mysql --version"

        result = self.run_command(cmd, capture_output=True)
        if result and result.returncode == 0:
            print(f"✓ MySQL 已安装: {result.stdout.strip()}")

            # 让用户输入密码
            print("\n检测到 MySQL 已安装，需要验证连接...")
            max_attempts = 3
            for attempt in range(max_attempts):
                user_password = self.get_mysql_password_from_user()

                # 测试连接
                if self.test_mysql_connection(user_password):
                    print("✓ MySQL 连接成功!")

                    # 更新密码
                    self.mysql_password = user_password

                    # 获取并显示端口号
                    port = self.get_mysql_port(user_password)
                    print(f"  MySQL 端口: {port}")
                    self.port=port

                    return True
                else:
                    if attempt < max_attempts - 1:
                        print(f"✗ 密码错误，请重试 ({attempt + 1}/{max_attempts})")
                    else:
                        print(f"✗ 密码错误次数过多，跳过 MySQL 配置")
                        print("  您可以稍后手动配置数据库")
                        return False

            return False
        else:
            print(f"✗ MySQL 未安装")
            return False

    def install_mysql_windows(self):
        """Windows MySQL 安装指导"""
        self.print_header("MySQL 未安装")

        print("\n请按照以下步骤手动安装 MySQL：")
        print("\n" + "="*60)
        print("  安装步骤：")
        print("="*60)
        print("\n1. 下载 MySQL 安装包")
        print("   下载地址: https://dev.mysql.com/downloads/mysql/")
        print("   选择: Windows (x86, 64-bit), MSI Installer")
        print("   建议选择较大的安装包（包含所有组件）")

        print("\n2. 运行安装程序")
        print("   - 选择安装类型: 【Developer Default】或【Server only】")
        print("   - 如果提示缺少 Visual Studio，请先安装:")
        print("     https://visualstudio.microsoft.com/zh-hans/downloads/")
        print("     (下载 Visual Studio Community，安装时选择 C++ 桌面开发)")

        print("\n3. 配置 MySQL Server")
        print("   - 端口: 使用默认 3306")
        print("   - Root 密码: 建议设置为 " + self.mysql_password)
        print("   - Windows Service: 勾选【Configure MySQL Server as a Windows Service】")
        print("   - 开机自启: 勾选【Start the MySQL Server at System Startup】")

        print("\n4. 添加到环境变量（重要）")
        print("   为了在命令行中直接使用 mysql 命令，需要添加到 PATH：")
        print("   - 右键【此电脑】→ 【属性】→ 【高级系统设置】")
        print("   - 点击【环境变量】按钮")
        print("   - 在【系统变量】中找到【Path】，双击编辑")
        print("   - 点击【新建】，添加 MySQL 的 bin 目录路径")
        print("     通常是: C:\\Program Files\\MySQL\\MySQL Server 9.x\\bin")
        print("   - 确定保存后，重新打开命令行窗口")

        print("\n5. 完成安装")
        print("   安装完成后，请重新运行本脚本进行配置验证")
        print("   运行命令: python setup_environment.py")

        print("\n" + "="*60)
        print("  注意事项：")
        print("="*60)
        print("  • 请记住您设置的 root 密码")
        print("  • 如果密码不是 " + self.mysql_password + "，需要修改 .env 配置文件")
        print("  • 安装完成后确保 MySQL 服务正在运行")
        print("  • 添加环境变量后需要重新打开命令行窗口才能生效")
        print("="*60)

        return False

    def install_mysql_linux(self):
        """Ubuntu 安装 MySQL"""
        print("\n--- Ubuntu MySQL 安装 ---")
        print("正在安装 MySQL Server...")

        try:
            # 更新包列表
            print("更新包列表...")
            self.run_command("sudo apt-get update", check=True)

            # 预配置 MySQL root 密码
            print("配置 MySQL root 密码...")
            debconf_cmd = f"echo 'mysql-server mysql-server/root_password password {self.mysql_password}' | sudo debconf-set-selections"
            self.run_command(debconf_cmd)
            debconf_cmd2 = f"echo 'mysql-server mysql-server/root_password_again password {self.mysql_password}' | sudo debconf-set-selections"
            self.run_command(debconf_cmd2)

            # 安装 MySQL
            print("安装 MySQL Server...")
            result = self.run_command("sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mysql-server", check=False)

            if result and result.returncode == 0:
                # 启动 MySQL 服务
                print("启动 MySQL 服务...")
                self.run_command("sudo systemctl start mysql")
                self.run_command("sudo systemctl enable mysql")

                print("✓ MySQL 安装成功!")
                return True
            else:
                print("✗ MySQL 安装失败，请手动安装")
                print("  sudo apt-get install mysql-server")
                return False

        except Exception as e:
            print(f"✗ MySQL 安装失败: {e}")
            return False

    def check_mysql_database(self):
        """检测 MySQL 数据库是否存在"""
        try:
            # 首先尝试使用密码连接
            if self.is_windows:
                cmd = f'mysql -u root -p{self.mysql_password} -e "SHOW DATABASES LIKE \'{self.db_name}\';"'
            else:
                cmd = f'mysql -u root -p{self.mysql_password} -e "SHOW DATABASES LIKE \'{self.db_name}\';"'

            result = self.run_command(cmd, check=False, capture_output=True)

            if result and result.returncode == 0:
                # 检查输出中是否包含数据库名
                if self.db_name in result.stdout:
                    return True
                return False
            else:
                # 如果失败，尝试使用 sudo（Linux）
                if self.is_linux:
                    cmd = f'sudo mysql -e "SHOW DATABASES LIKE \'{self.db_name}\';"'
                    result = self.run_command(cmd, check=False, capture_output=True)
                    if result and result.returncode == 0 and self.db_name in result.stdout:
                        return True
                return False

        except Exception as e:
            return False

    def create_mysql_database(self):
        """创建 MySQL 数据库"""
        self.print_header("检测/创建 MySQL 数据库")

        try:
            # 先检测数据库是否存在
            print(f"检测数据库: {self.db_name}")
            if self.check_mysql_database():
                print(f"✓ 数据库 '{self.db_name}' 已存在，跳过创建")
                return True

            print(f"数据库不存在，正在创建: {self.db_name}")

            if self.is_windows:
                # Windows: 使用 mysql 命令
                cmd = f'mysql -u root -p{self.mysql_password} -e "CREATE DATABASE IF NOT EXISTS {self.db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"'
            else:
                # Linux: 使用 mysql 命令
                cmd = f'mysql -u root -p{self.mysql_password} -e "CREATE DATABASE IF NOT EXISTS {self.db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"'

            result = self.run_command(cmd, check=False, capture_output=True)

            if result and result.returncode == 0:
                print(f"✓ 数据库 '{self.db_name}' 创建成功!")
                return True
            else:
                # 如果失败，尝试无密码连接（新安装的 MySQL）
                if self.is_linux:
                    print("尝试使用 sudo 权限创建数据库...")
                    cmd = f'sudo mysql -e "CREATE DATABASE IF NOT EXISTS {self.db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"'
                    result = self.run_command(cmd, check=False)
                    if result and result.returncode == 0:
                        print(f"✓ 数据库 '{self.db_name}' 创建成功!")

                        # 设置 root 密码
                        print("设置 root 密码...")
                        pwd_cmd = f"sudo mysql -e \"ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{self.mysql_password}'; FLUSH PRIVILEGES;\""
                        self.run_command(pwd_cmd)
                        return True

                print(f"✗ 数据库创建失败")
                print(f"请手动创建数据库:")
                print(f"  mysql -u root -p")
                print(f"  CREATE DATABASE {self.db_name} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
                return False

        except Exception as e:
            print(f"✗ 数据库创建失败: {e}")
            return False

    def print_mysql_password_change_guide(self):
        """打印 MySQL 密码修改指导"""
        self.print_header("MySQL 密码修改指导")

        print(f"\n当前 MySQL root 密码: {self.mysql_password}")
        print(f"数据库名称: {self.db_name}")
        print(f"数据库端口号:{self.port}")
        print("\n如需修改密码，请按以下步骤操作：")
        print("\n--- 方法 1: 使用 MySQL 命令行 ---")
        print("1. 登录 MySQL:")
        print(f"   mysql -u root -p{self.mysql_password}")
        print("\n2. 修改密码:")
        print("   ALTER USER 'root'@'localhost' IDENTIFIED BY '新密码';")
        print("   FLUSH PRIVILEGES;")
        print("   EXIT;")

        if self.is_linux:
            print("\n--- 方法 2: 使用 mysqladmin (Linux) ---")
            print(f"   mysqladmin -u root -p{self.mysql_password} password '新密码'")

        print("\n--- 修改应用配置 ---")
        print("修改密码后，请更新应用程序中的数据库配置：")
        print("  - 配置文件路径: .env 或 config.py")
        print("  - 数据库连接字符串格式:")
        print("    mysql://root:新密码@localhost:3306/trading_data")

    def check_redis(self):
        """检测 Redis 是否安装"""
        self.print_header("检测 Redis")

        if self.is_windows:
            # Windows 检测 redis-server.exe 或 redis-cli.exe
            cmd = "redis-cli --version"
        else:
            cmd = "redis-cli --version"

        result = self.run_command(cmd, capture_output=True)
        if result and result.returncode == 0:
            print(f"✓ Redis 已安装: {result.stdout.strip()}")
            return True
        else:
            print(f"✗ Redis 未安装")
            return False

    def install_redis_windows(self):
        """Windows 安装 Redis 指导"""
        print("\n--- Windows Redis 安装指导 ---")
        print("\n方法: 使用 Memurai (Redis 兼容)")
        print("   下载地址: https://github.com/tporadowski/redis/releases")
        print("下载msi 安装包，安装过程 勾选  Add the Redis installation folder to the PATH environment variable.")

        return False

    def install_redis_linux(self):
        """Ubuntu 安装 Redis"""
        print("\n--- Ubuntu Redis 安装 ---")
        print("正在安装 Redis Server...")

        try:
            # 更新包列表
            print("更新包列表...")
            self.run_command("sudo apt-get update", check=True)

            # 安装 Redis
            print("安装 Redis Server...")
            result = self.run_command("sudo apt-get install -y redis-server", check=False)

            if result and result.returncode == 0:
                # 配置 Redis（确保不使用密码）
                print("配置 Redis...")
                config_file = "/etc/redis/redis.conf"

                # 备份原配置
                self.run_command(f"sudo cp {config_file} {config_file}.backup")

                # 确保 Redis 绑定到 localhost 并且不需要密码
                self.run_command(f"sudo sed -i 's/^# requirepass.*/# requirepass/g' {config_file}")
                self.run_command(f"sudo sed -i 's/^requirepass.*/# requirepass/g' {config_file}")

                # 启动 Redis 服务
                print("启动 Redis 服务...")
                self.run_command("sudo systemctl restart redis-server")
                self.run_command("sudo systemctl enable redis-server")

                # 测试 Redis
                import time
                time.sleep(2)
                test_result = self.run_command("redis-cli ping", capture_output=True)
                if test_result and "PONG" in test_result.stdout:
                    print("✓ Redis 安装并启动成功!")
                    return True
                else:
                    print("✓ Redis 安装成功，但服务可能未正常启动")
                    print("  请手动检查: sudo systemctl status redis-server")
                    return True
            else:
                print("✗ Redis 安装失败，请手动安装")
                print("  sudo apt-get install redis-server")
                return False

        except Exception as e:
            print(f"✗ Redis 安装失败: {e}")
            return False

    def setup(self):
        """执行完整的环境配置流程"""
        print("\n" + "="*60)
        print("  自动化环境配置脚本")
        print("  支持系统: Windows / Ubuntu")
        print("="*60)
        print(f"\n当前系统: {platform.system()} {platform.release()}")
        print(f"Python 版本: {sys.version}")
        print(f"工作目录: {os.getcwd()}")

        if not (self.is_windows or self.is_linux):
            print(f"\n✗ 不支持的操作系统: {self.system}")
            print("  本脚本仅支持 Windows 和 Ubuntu/Linux 系统")
            return False

        # 1. 检测并创建 data 目录和 prompts.json
        self.check_and_create_data_directory()

        # 2. 检测并创建 .env 配置文件
        self.check_and_create_env_file()

        # 3. 检测并创建 venv
        if not self.check_venv():
            if not self.create_venv():
                print("\n⚠ 虚拟环境创建失败，但继续执行其他配置...")

        # 4. 检测并安装 MySQL
        mysql_installed = self.check_mysql()
        if not mysql_installed:
            if self.is_windows:
                self.install_mysql_windows()
            else:
                mysql_installed = self.install_mysql_linux()

        # 5. 创建 MySQL 数据库
        if mysql_installed:
            self.create_mysql_database()
            self.print_mysql_password_change_guide()

        # 6. 检测并安装 Redis
        redis_installed = self.check_redis()
        if not redis_installed:
            if self.is_windows:
                self.install_redis_windows()
            else:
                redis_installed = self.install_redis_linux()

        # 7. 安装 Trade 加密模块
        trade_installed = self.install_trade_wheel()

        # 打印最终总结
        self.print_header("环境配置总结")
        print(f"\n✓ Python 虚拟环境: {self.venv_path.absolute()}")
        print(f"  MySQL 状态: {'已安装' if mysql_installed else '需要手动安装'}")
        print(f"  Redis 状态: {'已安装' if redis_installed else '需要手动安装'}")
        print(f"  Trade 加密模块: {'已安装' if trade_installed else '未安装或跳过'}")

        if mysql_installed:
            print(f"\n  MySQL 配置:")
            print(f"    - 数据库: {self.db_name}")
            print(f"    - 用户: root")
            print(f"    - 密码: {self.mysql_password}")
            print(f"    - 连接: mysql://root:{self.mysql_password}@localhost:{self.port}/{self.db_name}")

        if redis_installed:
            print(f"\n  Redis 配置:")
            print(f"    - 地址: localhost:6379")
            print(f"    - 密码: 无")

        print("\n" + "="*60)
        print("  环境配置完成!")
        print("="*60)

        return True


if __name__ == "__main__":
    setup = EnvironmentSetup()
    try:
        success = setup.setup()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n用户中断执行")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
