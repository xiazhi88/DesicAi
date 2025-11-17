#!/bin/bash
# WSL 中安装 Python 3.12 脚本

set -e

echo "======================================"
echo "在 WSL 中安装 Python 3.12"
echo "======================================"

# 更新包列表
echo -e "\n[1/5] 更新软件包列表..."
sudo apt update

# 添加 deadsnakes PPA（包含最新 Python 版本）
echo -e "\n[2/5] 添加 deadsnakes PPA..."
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update

# 安装 Python 3.12
echo -e "\n[3/5] 安装 Python 3.12..."
sudo apt install -y python3.12 python3.12-venv python3.12-dev

# 安装 pip
echo -e "\n[4/5] 安装 pip for Python 3.12..."
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12

# 验证安装
echo -e "\n[5/5] 验证安装..."
python3.12 --version
python3.12 -m pip --version

echo -e "\n======================================"
echo "✓ Python 3.12 安装成功！"
echo "======================================"
echo ""
echo "使用方法："
echo "  python3.12 <script.py>         # 运行 Python 脚本"
echo "  python3.12 -m pip install ...  # 安装包"
echo "  python3.12 -m venv myenv       # 创建虚拟环境"
echo ""
echo "设置为默认 Python（可选）："
echo "  sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1"
echo "  sudo update-alternatives --config python3"
echo ""
