from pathlib import Path

from setuptools import Extension, find_packages, setup


PROJECT_ROOT = Path(__file__).resolve().parent
PRIVATE_TRADE_SOURCE = PROJECT_ROOT / "build_private" / "_trade.py"


def build_extensions():
    if not PRIVATE_TRADE_SOURCE.exists():
        return []

    from Cython.Build import cythonize

    return cythonize(
        [
            Extension(
                "src.api._trade",
                [str(PRIVATE_TRADE_SOURCE)],
            )
        ],
        compiler_directives={"language_level": "3"},
    )


setup(
    name="DesicAi-okx",
    version="0.1.0",
    description="DesicAi - AI Quantitative Trading System for OKX",
    long_description=(PROJECT_ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    author="xiazhi",
    author_email="desicai@163.com",
    url="https://github.com/xiazhi88/DesicAi",
    license="MIT",
    packages=find_packages(include=["src", "src.*"]),
    ext_modules=build_extensions(),
    python_requires=">=3.8,<3.15",
    install_requires=[
        "requests>=2.28.0",
        "websocket-client>=1.5.0",
        "pandas>=1.3.0",
        "numpy>=1.20.0",
        "pymysql>=1.0.0",
        "redis>=4.5.0",
        "python-dotenv>=1.0.0",
        "loguru>=0.7.0",
        "Flask>=3.0.0",
        "Flask-CORS>=4.0.0",
        "pytz>=2023.3",
        "psutil>=5.8.0",
        "tqdm>=4.60.0",
        "cryptography>=3.4.0",
        "fastapi>=0.68.0",
        "uvicorn>=0.15.0",
        "DBUtils",
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    ],
)
