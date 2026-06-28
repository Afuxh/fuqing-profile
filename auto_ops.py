"""
自动运维脚本 — 热点聚合站每日生成 + 多平台部署
替代原 QoderWork 已丢失的 auto_ops.py

用法:
  python auto_ops.py generate    # 仅生成 site/
  python auto_ops.py deploy      # 仅部署（git push → GitHub Pages）
  python auto_ops.py full        # 生成 + 部署（完整流程）
  python auto_ops.py status      # 状态检查
"""
import argparse
import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("auto_ops")

REPO_ROOT = Path(__file__).parent
SITE_DIR = REPO_ROOT / "site"
TZ_SHANGHAI = timezone(timedelta(hours=8))


def now_str() -> str:
    return datetime.now(TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M")


def step_generate() -> bool:
    """运行 generate_v3.7.py 生成 site/ 目录"""
    logger.info("=" * 50)
    logger.info(f"开始生成热点站 [{now_str()}]")
    logger.info("=" * 50)

    result = subprocess.run(
        [sys.executable, "generate_v3.7.py", "-c", "config.yaml", "-o", "site", "-m", "web", "--update-only"],
        cwd=str(REPO_ROOT),
        capture_output=False,
        timeout=600,
    )
    if result.returncode != 0:
        logger.error(f"生成失败，退出码: {result.returncode}")
        return False

    # 检查产物
    hot_html = SITE_DIR / "hot-news.html"
    if not hot_html.exists():
        logger.error("site/hot-news.html 未生成")
        return False

    size_kb = hot_html.stat().st_size / 1024
    logger.info(f"生成成功: site/hot-news.html ({size_kb:.1f} KB)")
    return True


def step_copy_to_root() -> bool:
    """将 site/ 下的核心产物复制到仓库根目录（用于 GitHub Pages 部署）"""
    files_to_copy = [
        ("site/hot-news.html", "hot-news.html"),
        ("site/feed.xml", "feed.xml"),
        ("site/sitemap.xml", "sitemap.xml"),
        ("site/sources.opml", "sources.opml"),
        ("site/api.json", "api.json"),
    ]
    copied = 0
    for src, dst in files_to_copy:
        src_path = REPO_ROOT / src
        dst_path = REPO_ROOT / dst
        if src_path.exists():
            shutil.copy2(src_path, dst_path)
            copied += 1
            logger.info(f"复制: {src} → {dst}")
        else:
            logger.warning(f"跳过（不存在）: {src}")

    logger.info(f"共复制 {copied}/{len(files_to_copy)} 个文件到根目录")
    return copied > 0


def step_deploy_github() -> bool:
    """git add + commit + push 触发 GitHub Pages 部署"""
    logger.info("准备推送到 GitHub...")

    # 检查是否有变更
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    if not result.stdout.strip():
        logger.info("无变更，跳过推送")
        return True

    # git add
    subprocess.run(
        ["git", "add", "site/", "hot-news.html", "feed.xml", "sitemap.xml", "sources.opml", "api.json", "data/"],
        cwd=str(REPO_ROOT), capture_output=True, timeout=30,
    )

    # git commit
    commit_msg = f"auto: 每日热点更新 [{now_str()}]"
    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 and "nothing to commit" not in result.stdout:
        logger.warning(f"提交警告: {result.stderr.strip()}")

    # git push
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        logger.error(f"推送失败: {result.stderr.strip()}")
        return False

    logger.info("推送成功，GitHub Actions 将自动部署")
    return True


def step_deploy_edgeone() -> bool:
    """部署到 EdgeOne Pages（如 CLI 可用）"""
    try:
        subprocess.run(["edgeone", "--version"], capture_output=True, timeout=5)
    except Exception:
        logger.info("EdgeOne CLI 未安装，跳过")
        return False

    result = subprocess.run(
        ["edgeone", "pages", "deploy", str(SITE_DIR), "--name", "fuqing-profile", "--env", "production", "--area", "global"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    if result.returncode == 0:
        logger.info("EdgeOne Pages 部署成功")
        return True
    else:
        logger.warning(f"EdgeOne 部署失败: {result.stderr.strip()}")
        return False


def run_full() -> bool:
    """完整流程: 生成 → 复制 → 多平台部署"""
    overall_start = time.time()

    if not step_generate():
        return False
    if not step_copy_to_root():
        return False

    # 并行记录各平台部署结果
    results = {}
    results["github"] = step_deploy_github()
    results["edgeone"] = step_deploy_edgeone()

    elapsed = time.time() - overall_start
    logger.info(f"全流程完成，耗时 {elapsed:.0f}s")
    for platform, ok in results.items():
        logger.info(f"  {'✅' if ok else '❌'} {platform}")

    return results["github"]


def run_status():
    """状态检查"""
    print(f"\n运维状态 [{now_str()}]\n")

    # Git 状态
    result = subprocess.run(
        ["git", "log", "--oneline", "-5"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
    )
    print("最近提交:")
    for line in result.stdout.strip().split("\n"):
        print(f"  {line}")

    # 产物状态
    for f in ["site/hot-news.html", "hot-news.html"]:
        p = REPO_ROOT / f
        if p.exists():
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
            size = p.stat().st_size / 1024
            print(f"  {f}: {size:.0f}KB (更新于 {mtime})")
        else:
            print(f"  {f}: 不存在")


def main():
    parser = argparse.ArgumentParser(description="热点聚合站自动运维")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("generate", help="仅生成 site/")
    subparsers.add_parser("deploy", help="仅部署（复制+推送）")
    subparsers.add_parser("full", help="生成 + 部署（完整流程）")
    subparsers.add_parser("status", help="查看运维状态")

    args = parser.parse_args()

    if args.command == "generate":
        ok = step_generate()
        sys.exit(0 if ok else 1)
    elif args.command == "deploy":
        ok = step_copy_to_root() and step_deploy_github()
        sys.exit(0 if ok else 1)
    elif args.command == "full":
        ok = run_full()
        sys.exit(0 if ok else 1)
    elif args.command == "status":
        run_status()
    else:
        # 默认 full
        ok = run_full()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
