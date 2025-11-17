# 多平台构建指南

## 当前状态

✅ **Windows wheels** - 已构建（13 个文件）
❌ **Linux wheels** - 需要 Docker 或 Linux 机器
❌ **macOS wheels** - 需要 macOS 机器

## 方案 1: 使用 Docker 构建 Linux wheels（Windows 上可行）

### 前置条件
- 安装 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)

### 步骤

#### 方法 A: 使用 Dockerfile（推荐）

```bash
# 1. 构建 Docker 镜像
docker build -t aiquant-builder .

# 2. 运行容器构建 Linux wheels
docker run --rm -v "%cd%/wheelhouse:/app/wheelhouse" aiquant-builder

# 构建完成后，wheelhouse/ 中会新增 Linux wheels
```

#### 方法 B: 直接使用 cibuildwheel 官方镜像

```bash
# 使用 cibuildwheel 官方 Docker 支持
docker run --rm -v "%cd%:/app" -w /app ^
  quay.io/pypa/manylinux2014_x86_64 ^
  bash -c "pip install cibuildwheel && python -m cibuildwheel --platform linux --output-dir wheelhouse"
```

#### 方法 C: 使用 WSL2（如果已安装）

```bash
# 在 WSL2 中运行
wsl

# 在 WSL 中执行
cd /mnt/g/aiQuant
pip install cibuildwheel
python -m cibuildwheel --platform linux --output-dir wheelhouse
```

## 方案 2: 在 Linux 机器上构建

如果你有 Linux 服务器或虚拟机：

```bash
# 安装依赖
pip install cibuildwheel

# 构建
python -m cibuildwheel --platform linux --output-dir wheelhouse

# 或使用构建脚本
chmod +x build_linux.sh
./build_linux.sh
```

## 方案 3: 在 macOS 机器上构建

如果你有 Mac 电脑：

```bash
# 安装依赖
pip install cibuildwheel

# 构建（包含 Intel 和 Apple Silicon）
python -m cibuildwheel --platform macos --output-dir wheelhouse

# 查看构建结果
ls -lh wheelhouse/*.whl
```

会生成类似：
```
desicai_okx-0.1.0-cp311-cp311-macosx_10_9_x86_64.whl
desicai_okx-0.1.0-cp311-cp311-macosx_11_0_arm64.whl
desicai_okx-0.1.0-cp311-cp311-macosx_10_9_universal2.whl
```

## 方案 4: 使用 GitHub Actions（自动化）

虽然你删除了 GitHub Actions，但这是最简单的多平台构建方法。如果需要，可以随时添加回来。

优势：
- ✅ 自动构建所有平台
- ✅ 免费（公开仓库）
- ✅ 无需本地 Docker/虚拟机
- ✅ 每次 push 自动触发

只需在 `.github/workflows/` 添加配置文件即可。

## 方案 5: 使用云构建服务

### CircleCI / Travis CI / Azure Pipelines
提供多平台构建环境，配置类似 GitHub Actions。

### 腾讯云 / 阿里云构建服务
如果在国内，可以使用国内云服务的 CI/CD 功能。

## 推荐方案总结

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| Docker (方案1) | 本地可控，无需额外机器 | 需要安装 Docker | ⭐⭐⭐⭐⭐ |
| WSL2 | Windows 原生支持 | 需要启用 WSL2 | ⭐⭐⭐⭐ |
| Linux 服务器 | 构建速度快 | 需要额外机器 | ⭐⭐⭐ |
| macOS 机器 | 官方环境 | 需要 Mac 硬件 | ⭐⭐⭐ |
| GitHub Actions | 全自动，零配置 | 需要推送到 GitHub | ⭐⭐⭐⭐⭐ |

## 验证构建结果

构建完成后，检查 wheelhouse/ 目录：

```bash
# Windows
dir wheelhouse

# Linux/Mac
ls -lh wheelhouse/

# 应该看到：
# - desicai_okx-0.1.0-cp3XX-cp3XX-win_amd64.whl (Windows)
# - desicai_okx-0.1.0-cp3XX-cp3XX-manylinux_2_17_x86_64.whl (Linux)
# - desicai_okx-0.1.0-cp3XX-cp3XX-macosx_XX_X_x86_64.whl (macOS)
```

## 完整构建目标

**总共需要构建约 60+ 个 wheels**：

- Windows: ~13 wheels (已完成 ✅)
- Linux: ~20 wheels (x86_64 + aarch64)
- macOS: ~30 wheels (x86_64 + arm64 + universal2)

## 需要帮助？

如果希望我帮你配置 Docker 或恢复 GitHub Actions，请告诉我！
