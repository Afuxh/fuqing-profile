"""
热点聚合站 — 多平台部署脚本
支持 EdgeOne Pages（国内首选）+ GitHub Pages（国际备用）

用法:
  python deploy.py edgeone              # 部署到 EdgeOne Pages
  python deploy.py edgeone --init       # 首次初始化 EdgeOne 项目
  python deploy.py github               # 部署到 GitHub Pages（git push）
  python deploy.py all                  # 部署到所有平台
  python deploy.py status               # 查看各平台部署状态
"""
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("deploy")

# ============ 路径配置 ============

REPO_ROOT = Path(__file__).parent
SITE_DIR = REPO_ROOT / "site"
CONFIG_PATH = REPO_ROOT / "config.yaml"


def load_config() -> dict:
    """加载部署配置"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


# ============ EdgeOne Pages 部署 ============

def deploy_edgeone(init: bool = False) -> bool:
    """
    部署到腾讯云 EdgeOne Pages
    
    前置条件:
    1. npm install -g edgeone （安装 CLI）
    2. edgeone login （首次登录）
    3. edgeone pages project create <name> （首次创建项目）
    """
    if not SITE_DIR.exists():
        logger.error(f"site/ 目录不存在: {SITE_DIR}")
        logger.info("请先运行 generate_v3.7.py 生成站点")
        return False
    
    # 检查 edgeone CLI 是否安装
    try:
        result = subprocess.run(
            ["edgeone", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise FileNotFoundError
        logger.info(f"EdgeOne CLI: {result.stdout.strip()}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.error("EdgeOne CLI 未安装")
        logger.info("安装步骤:")
        logger.info("  1. npm install -g edgeone")
        logger.info("  2. edgeone login")
        return False
    
    if init:
        return _edgeone_init()
    
    return _edgeone_deploy()


def _edgeone_init() -> bool:
    """首次初始化 EdgeOne Pages 项目（通过 deploy 自动创建）"""
    logger.info("正在初始化 EdgeOne Pages 项目...")
    logger.info("EdgeOne Pages 会在首次 deploy 时自动创建项目，无需手动 init")
    logger.info("直接运行: python deploy.py edgeone")
    return True


def _edgeone_deploy() -> bool:
    """部署到 EdgeOne Pages"""
    logger.info("正在部署到 EdgeOne Pages...")
    
    config = load_config()
    deploy_cfg = config.get("deployment", {}).get("primary", {})
    project_name = deploy_cfg.get("project_name", "fuqing-profile")
    
    # 方式1: 通过 edgeone pages deploy 命令
    result = subprocess.run(
        ["edgeone", "pages", "deploy",
         str(SITE_DIR),               # 部署目录（位置参数）
         "--name", project_name,       # 项目名
         "--env", "production",        # 生产环境
         "--area", "global"],          # 全球加速
        capture_output=True, text=True, timeout=120,
    )
    
    if result.returncode == 0:
        output = result.stdout.strip()
        logger.info(f"EdgeOne Pages 部署成功!")
        logger.info(f"输出: {output}")
        
        # 提取部署 URL
        for line in output.split("\n"):
            if "edgeone.cool" in line or "http" in line:
                logger.info(f"访问地址: {line.strip()}")
                break
        return True
    else:
        logger.error(f"EdgeOne Pages 部署失败: {result.stderr.strip()}")
        # 尝试备选方式
        return _edgeone_deploy_api()


def _edgeone_deploy_api() -> bool:
    """通过 API Token 部署（备选方案）"""
    api_token = os.environ.get("EDGEONE_PAGES_API_TOKEN", "")
    if not api_token:
        logger.info("EDGEONE_PAGES_API_TOKEN 未设置，无法使用 API 部署")
        logger.info("请访问 https://edgeone.ai 获取 API Token")
        return False
    
    config = load_config()
    project_name = config.get("deployment", {}).get("primary", {}).get("project_name", "fuqing-profile")
    
    try:
        import urllib.request
        
        # 打包 site/ 目录为 zip
        import zipfile
        import io
        
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for filepath in SITE_DIR.rglob("*"):
                if filepath.is_file():
                    arcname = filepath.relative_to(SITE_DIR)
                    zf.write(filepath, arcname)
        
        zip_data = zip_buffer.getvalue()
        logger.info(f"打包完成: {len(zip_data)} bytes, {sum(1 for _ in SITE_DIR.rglob('*') if _.is_file())} 文件")
        
        # 上传到 EdgeOne Pages
        req = urllib.request.Request(
            f"https://api.edgeone.ai/v1/pages/projects/{project_name}/deployments",
            data=zip_data,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/zip",
            },
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            url = result.get("url", result.get("deployment", {}).get("url", ""))
            logger.info(f"API 部署成功: {url}")
            return True
            
    except Exception as e:
        logger.error(f"API 部署失败: {e}")
        return False


# ============ GitHub Pages 部署 ============

def deploy_github() -> bool:
    """通过 git push 部署到 GitHub Pages"""
    logger.info("正在部署到 GitHub Pages...")
    
    # 确保 site/ 目录有内容
    if not (SITE_DIR / "hot-news.html").exists():
        logger.error("site/hot-news.html 不存在，请先生成站点")
        return False
    
    # git add + commit + push
    steps = [
        (["git", "add", "site/"], "暂存 site/ 目录"),
        (["git", "commit", "-m", f"deploy: 热点聚合站更新 {time.strftime('%Y-%m-%d %H:%M')}"], "提交变更"),
        (["git", "push", "origin", "main"], "推送到 GitHub"),
    ]
    
    for cmd, desc in steps:
        logger.info(f"  {desc}...")
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=60)
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            logger.error(f"  失败: {result.stderr.strip()}")
            return False
    
    logger.info("GitHub Pages 部署完成（等待 GitHub Actions 构建）")
    logger.info("访问: https://afuxh.github.io/fuqing-profile/hot-news.html")
    return True


# ============ Cloudflare Pages 部署 ============

def deploy_cloudflare() -> bool:
    """部署到 Cloudflare Pages"""
    logger.info("正在部署到 Cloudflare Pages...")
    
    # 检查 wrangler CLI
    try:
        result = subprocess.run(
            ["npx", "wrangler", "--version"],
            capture_output=True, text=True, timeout=15,
        )
        logger.info(f"Wrangler: {result.stdout.strip()}")
    except Exception:
        logger.error("Wrangler CLI 不可用")
        logger.info("安装: npm install -g wrangler")
        return False
    
    config = load_config()
    project_name = config.get("deployment", {}).get("mirror", {}).get("project_name", "fuqing-profile")
    
    result = subprocess.run(
        ["npx", "wrangler", "pages", "deploy", str(SITE_DIR),
         "--project-name", project_name],
        capture_output=True, text=True, timeout=120,
    )
    
    if result.returncode == 0:
        logger.info(f"Cloudflare Pages 部署成功!")
        logger.info(f"输出: {result.stdout.strip()}")
        return True
    else:
        logger.error(f"Cloudflare Pages 部署失败: {result.stderr.strip()}")
        return False


# ============ 状态查看 ============

def show_status():
    """显示各平台部署状态"""
    config = load_config()
    deploy_cfg = config.get("deployment", {})
    
    print("\n📊 热点聚合站 — 部署平台状态\n")
    print(f"{'平台':<20} {'角色':<10} {'URL':<50} {'状态':<10}")
    print("-" * 95)
    
    platforms = {
        "primary": ("EdgeOne Pages", "国内首选"),
        "mirror": ("GitHub Pages", "国际备用"),
        "legacy": ("GitHub Pages", "保留"),
    }
    
    for key, (name, role) in platforms.items():
        cfg = deploy_cfg.get(key, {})
        url = cfg.get("url", "(未配置)")
        platform = cfg.get("platform", "")
        
        # 检查可用性
        if key == "primary":
            try:
                r = subprocess.run(["edgeone", "--version"], capture_output=True, timeout=5)
                status = "✅ 就绪" if r.returncode == 0 else "❌ CLI未装"
            except:
                status = "❌ CLI未装"
        elif key == "mirror":
            try:
                r = subprocess.run(["npx", "wrangler", "--version"], capture_output=True, timeout=10)
                status = "✅ 就绪" if r.returncode == 0 else "❌ CLI未装"
            except:
                status = "❌ CLI未装"
        else:
            status = "✅ 在线" if url else "❌ 未配置"
        
        print(f"{name:<18} {role:<10} {url:<50} {status}")
    
    # 站点文件统计
    if SITE_DIR.exists():
        html_count = sum(1 for f in SITE_DIR.glob("*.html"))
        total_size = sum(f.stat().st_size for f in SITE_DIR.rglob("*") if f.is_file())
        print(f"\n📁 site/ 目录: {html_count} 个 HTML 文件, {total_size / 1024:.1f} KB")
    else:
        print(f"\n📁 site/ 目录不存在，请先运行 generate_v3.7.py")
    
    print(f"\n💡 快速部署: python deploy.py edgeone  (国内)")
    print(f"             python deploy.py github    (海外)")
    print(f"             python deploy.py all       (全部)")


# ============ 主入口 ============

def main():
    parser = argparse.ArgumentParser(description="热点聚合站多平台部署")
    subparsers = parser.add_subparsers(dest="command")
    
    # edgeone 命令
    eo_parser = subparsers.add_parser("edgeone", help="部署到 EdgeOne Pages")
    eo_parser.add_argument("--init", action="store_true", help="首次初始化项目")
    
    # github 命令
    subparsers.add_parser("github", help="部署到 GitHub Pages")
    
    # cloudflare 命令
    subparsers.add_parser("cloudflare", help="部署到 Cloudflare Pages")
    
    # all 命令
    subparsers.add_parser("all", help="部署到所有平台")
    
    # status 命令
    subparsers.add_parser("status", help="查看部署状态")
    
    args = parser.parse_args()
    
    if args.command == "edgeone":
        success = deploy_edgeone(init=args.init)
        sys.exit(0 if success else 1)
    elif args.command == "github":
        success = deploy_github()
        sys.exit(0 if success else 1)
    elif args.command == "cloudflare":
        success = deploy_cloudflare()
        sys.exit(0 if success else 1)
    elif args.command == "all":
        results = {}
        results["edgeone"] = deploy_edgeone()
        results["github"] = deploy_github()
        results["cloudflare"] = deploy_cloudflare()
        
        print("\n📊 部署结果汇总:")
        for platform, ok in results.items():
            print(f"  {'✅' if ok else '❌'} {platform}")
        
        sys.exit(0 if all(results.values()) else 1)
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
