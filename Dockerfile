FROM python:3.11-slim

WORKDIR /app

# 安装构建依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 构建工具
RUN pip install --no-cache-dir \
    Cython>=3.0.0 \
    cibuildwheel \
    setuptools \
    wheel

# 复制项目文件
COPY . .

# 构建 wheels
CMD ["python", "-m", "cibuildwheel", "--platform", "linux", "--output-dir", "wheelhouse"]
