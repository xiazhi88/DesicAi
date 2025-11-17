#!/bin/bash
# 使用 Docker 直接构建 Linux 版本的 wheel

echo "正在使用 Docker 构建 Linux 版本..."
echo ""

# 检查 Docker 是否运行
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker 未运行，请先启动 Docker"
    exit 1
fi

echo "✓ Docker 已运行"
echo ""
echo "开始构建 Linux wheel..."
echo "这可能需要几分钟时间..."
echo ""

# 直接在 manylinux 容器中为每个 Python 版本构建
docker run --rm -v "$(pwd)":/project -w /project \
    quay.io/pypa/manylinux2014_x86_64 \
    bash -c "for PYBIN in /opt/python/cp{38,39,310,311,312,313,314}*/bin; do \
        echo \"Building for \$PYBIN/python...\"; \
        \$PYBIN/pip install -q Cython wheel setuptools && \
        \$PYBIN/python setup.py bdist_wheel -d wheelhouse_temp || exit 1; \
    done && \
    auditwheel repair wheelhouse_temp/*.whl -w wheelhouse && \
    rm -rf wheelhouse_temp build src/api/trade.c && \
    echo 'All wheels built successfully!'"

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Linux wheel 构建完成！"
    echo ""
    echo "构建的文件:"
    ls -lh wheelhouse/*manylinux*.whl
else
    echo ""
    echo "❌ 构建失败"
    exit 1
fi
