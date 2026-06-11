"""
热点聚合网站运维工具 v1.7.0
=====================
用法：
  python ops_tool.py check      # 完整性检查
  python ops_tool.py diagnose   # 环境诊断
  python ops_tool.py fix        # 常见问题自动修复
  python ops_tool.py history    # 运行历史分析
  python ops_tool.py quick      # 快速运维（check + diagnose + 建议）
  python ops_tool.py git        # Git健康检查和修复

新功能检查项：
  - RSS 订阅源 (site/feed.xml)
  - JSON API (site/api.json)
  - 站点地图 (site/sitemap.xml)
  - OPML 订阅列表 (site/sources.opml)
  - 数据快照 (data/latest.json, data/snapshot_*.json)
  - 趋势分析器 (trend_analyzer.py)
  - 多站点配置 (sites.yaml)
  - 数据源数量 (25个)
  - Git仓库健康检查
"""
import os
import sys
import re
import json
import time
import logging
import argparse
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter

# 导入版本信息（单一数据源）
try:
    from version import __version__, OPS_TOOL_VERSION, VERSION_HISTORY
except ImportError:
    OPS_TOOL_VERSION = "1.7.0"
    VERSION_HISTORY = []

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
PROJECT_DIR = Path(__file__).parent


# ============ 进度条工具 ============
class ProgressBar:
    """简易终端进度条，支持时间预估"""
    
    def __init__(self, total: int, desc: str = "", width: int = 40):
        self.total = max(total, 1)
        self.current = 0
        self.desc = desc
        self.width = width
        self.start_time = time.time()
        self.last_update = 0
    
    def update(self, n: int = 1):
        self.current = min(self.current + n, self.total)
        now = time.time()
        if now - self.last_update < 0.1 and self.current < self.total:
            return
        self.last_update = now
        self._render()
    
    def _render(self):
        pct = self.current / self.total
        filled = int(self.width * pct)
        bar = "█" * filled + "░" * (self.width - filled)
        elapsed = time.time() - self.start_time
        if pct > 0 and pct < 1:
            eta = elapsed / pct - elapsed
            eta_str = f" 预计剩余 {self._fmt_time(eta)}"
        elif pct >= 1:
            eta_str = f" 用时 {self._fmt_time(elapsed)}"
        else:
            eta_str = ""
        sys.stderr.write(f"\r  {self.desc} [{bar}] {self.current}/{self.total} ({pct*100:.0f}%){eta_str}   ")
        sys.stderr.flush()
        if self.current >= self.total:
            sys.stderr.write("\n")
    
    @staticmethod
    def _fmt_time(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            return f"{seconds/60:.0f}分{seconds%60:.0f}秒"
        else:
            h = int(seconds / 3600)
            m = int((seconds % 3600) / 60)
            return f"{h}时{m}分"


# ============ 工具函数 ============
def _read_html() -> str:
    """读取生成的 HTML"""
    html_path = PROJECT_DIR / "site" / "hot-news.html"
    if not html_path.exists():
        logger.error(f"HTML 文件不存在: {html_path}")
        sys.exit(1)
    return html_path.read_text(encoding="utf-8")


def _load_config() -> dict:
    """加载配置"""
    import yaml
    config_path = PROJECT_DIR / "config.yaml"
    if not config_path.exists():
        logger.error("config.yaml 不存在")
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _count_in_html(pattern: str, html: str, label: str) -> int:
    count = len(re.findall(pattern, html))
    logger.info(f"  {label}: {count}")
    return count


# ============ check: 生成后完整性检查 ============
def check():
    """检查生成的 HTML 完整性"""
    logger.info("=" * 50)
    logger.info("  🔍 完整性检查")
    logger.info("=" * 50)
    
    check_items = 13  # 结构/内容/翻译/AI/评分/功能/链接/新输出/模板卡片/emoji重复/AI内容质量/AI空容器/部署验证
    pbar = ProgressBar(check_items, "🔍 完整性检查")
    
    start_time = time.time()
    html = _read_html()
    config = _load_config()
    
    issues = []
    warnings = []
    
    # --- 1. 结构完整性 ---
    logger.info("\n📋 结构检查:")
    
    required_elements = [
        ("<html", "HTML 标签"),
        ("</html>", "HTML 闭合"),
        ("<head>", "head 标签"),
        ("<body>", "body 标签"),
        ("<style>", "内联样式"),
        ("<script>", "JavaScript"),
    ]
    for elem, label in required_elements:
        if elem not in html:
            issues.append(f"缺少 {label}")
            logger.warning(f"  ❌ 缺少 {label}")
    
    pbar.update(1)
    
    # --- 2. 内容数量 ---
    logger.info("\n📊 内容统计:")
    card_count = _count_in_html(r'class="topic-card"', html, "卡片总数")
    level_names = [l["name"] for l in config.get("scoring", {}).get("levels", [])]
    
    for name in level_names:
        cnt = _count_in_html(f'data-grade="{name}"', html, f"  [{name}]")
        if cnt == 0:
            warnings.append(f"档位 [{name}] 无内容")
    
    pbar.update(1)
    
    # --- 3. 翻译覆盖率 ---
    logger.info("\n🌐 翻译检查:")
    zh_summary_count = _count_in_html(r'class="[^"]*\bzh-summary\b[^"]*"', html, "中文小结数")
    total_original = len(re.findall(r'class="summary"', html))
    if total_original > 0:
        rate = zh_summary_count / total_original * 100
        logger.info(f"  翻译覆盖率: {rate:.1f}%")
        if rate < 30:
            warnings.append(f"翻译覆盖率偏低 ({rate:.1f}%)，检查 translate.enabled 和网络")
    
    pbar.update(1)
    
    # --- 4. 视角评论/行动引导 ---
    logger.info("\n💡 AI评论检查:")
    perspective_count = _count_in_html(r'class="[^"]*\bperspective-comment\b[^"]*"', html, "视角评论")
    action_count = _count_in_html(r'class="[^"]*\baction-guidance\b[^"]*"', html, "行动引导")
    
    ai_enabled = config.get("ai_insights", {}).get("enabled", False)
    if ai_enabled and perspective_count == 0:
        warnings.append("AI评论已启用但视角评论为空（检查API配置或Skill模式占位）")
    
    # 价值标签检查
    tag_count = _count_in_html(r'class="[^"]*\bvalue-tag\b[^"]*"', html, "价值标签")
    cards_with_tags = len(re.findall(r'data-tags="([^"]+)"', html))
    cards_with_valid_tags = len(re.findall(r'data-tags="([^"]{2,})"', html))
    if cards_with_tags > 0:
        logger.info(f"  价值标签: {tag_count} 个标签实例, {cards_with_valid_tags} 张卡片含标签")
        if ai_enabled and cards_with_valid_tags == 0:
            warnings.append("AI评论已启用但价值标签为空（新标签API可能未返回数据）")
    
    pbar.update(1)
    
    # --- 5. 评分分布 ---
    logger.info("\n📈 评分分布:")
    score_pattern = r'(\d+(?:\.\d+)?)分'
    scores = [float(s) for s in re.findall(score_pattern, html)]
    if scores:
        logger.info(f"  总分条数: {len(scores)}")
        logger.info(f"  最高分: {max(scores):.1f}")
        logger.info(f"  最低分: {min(scores):.1f}")
        logger.info(f"  平均分: {sum(scores)/len(scores):.1f}")
        
        # 分布检查
        high = sum(1 for s in scores if s >= 80)
        mid = sum(1 for s in scores if 70 <= s < 80)
        low = sum(1 for s in scores if 60 <= s < 70)
        logger.info(f"  分布: 高分(>=80):{high} 中分(70-79):{mid} 低分(60-69):{low}")
        
        if high == 0 and len(scores) > 5:
            warnings.append("无高分内容(>=80)，考虑调整关键词或阈值")
    
    pbar.update(1)
    
    # --- 6. 功能元素 ---
    logger.info("\n🔧 功能检查:")
    _count_in_html(r'searchInput', html, "搜索框")
    _count_in_html(r'themeToggle', html, "主题切换")
    _count_in_html(r'bookmark-btn', html, "收藏按钮")
    _count_in_html(r'copy-btn', html, "复制按钮")
    _count_in_html(r'new-badge', html, "NEW角标")
    
    pbar.update(1)
    
    # --- 7. 链接有效性（检测死链占位）---
    logger.info("\n🔗 链接检查:")
    href_count = _count_in_html(r'href="', html, "链接总数")
    empty_href = len(re.findall(r'href=""', html))
    if empty_href > 0:
        warnings.append(f"有 {empty_href} 个空链接")
        logger.warning(f"  ⚠️ 空链接: {empty_href}")
    
    placeholder_href = len(re.findall(r'href="(?:#|javascript:void\(0\))"', html))
    if placeholder_href > 0:
        logger.info(f"  占位链接: {placeholder_href}")
    
    pbar.update(1)
    
    # --- 8. 新输出文件检查 ---
    logger.info("\n📄 新输出文件检查:")
    
    new_outputs = [
        ("site/feed.xml", "RSS 订阅源"),
        ("site/api.json", "JSON API"),
        ("site/sitemap.xml", "站点地图"),
        ("site/sources.opml", "OPML 订阅列表"),
        ("data/latest.json", "数据快照"),
    ]
    
    for path_str, label in new_outputs:
        file_path = PROJECT_DIR / path_str
        if file_path.exists():
            size = file_path.stat().st_size
            if path_str.endswith('.json'):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        logger.info(f"  ✅ {label}: {len(data)} 条记录")
                    elif isinstance(data, dict):
                        logger.info(f"  ✅ {label}: {len(data)} 个字段")
                    else:
                        logger.info(f"  ✅ {label}: {size} 字节")
                except Exception:
                    logger.info(f"  ✅ {label}: {size} 字节")
            else:
                logger.info(f"  ✅ {label}: {size} 字节")
        else:
            warnings.append(f"{label} 不存在 ({path_str})")
            logger.warning(f"  ❌ {label} 不存在")
    
    # 检查数据快照文件
    snapshot_files = list((PROJECT_DIR / "data").glob("snapshot_*.json")) if (PROJECT_DIR / "data").exists() else []
    if snapshot_files:
        logger.info(f"  ✅ 历史快照: {len(snapshot_files)} 个")
    else:
        logger.info(f"  ⚠️ 无历史快照文件")
    
    pbar.update(1)
    
    # --- 9. 模板卡片检查 ---
    logger.info("\n🧹 模板卡片检查:")
    template_cards = 0
    card_pattern = re.compile(r'<div class="topic-card"[^>]*>(.*?)</div>\s*</div>', re.DOTALL)
    for card_match in card_pattern.finditer(html):
        card_html = card_match.group(1)
        title_m = re.search(r'<h2[^>]*>(.*?)</h2>', card_html)
        source_m = re.search(r'class="source-name"[^>]*>(.*?)<', card_html)
        summary_m = re.search(r'class="summary"[^>]*>(.*?)<', card_html)
        if title_m and source_m:
            title_text = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
            source_text = re.sub(r'<[^>]+>', '', source_m.group(1)).strip()
            if title_text == source_text and not summary_m:
                template_cards += 1
    if template_cards > 0:
        issues.append(f"发现 {template_cards} 个模板卡片（标题=来源名且无摘要），检查generate_v3.7.py过滤逻辑")
        logger.warning(f"  ❌ 模板卡片: {template_cards} 个（应被过滤）")
    else:
        logger.info(f"  ✅ 无模板卡片残留")
    pbar.update(1)
    
    # --- 10. emoji重复检查 ---
    logger.info("\n🔍 emoji重复检查:")
    emoji_pattern = re.compile(r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF]')
    ai_labels = re.findall(r'<span class="ai-label">(.*?)</span>', html)
    emoji_in_labels = 0
    for label_text in ai_labels:
        if emoji_pattern.search(label_text):
            emoji_in_labels += 1
    if emoji_in_labels > 0:
        issues.append(f"发现 {emoji_in_labels} 个.ai-label包含emoji文本（应改用CSS ::before伪元素）")
        logger.warning(f"  ❌ emoji重复: {emoji_in_labels} 个标签含emoji文本")
    else:
        logger.info(f"  ✅ .ai-label无emoji文本（正确使用CSS伪元素）")
    pbar.update(1)
    
    # --- 11. AI内容质量检查 ---
    logger.info("\n🧪 AI内容质量检查:")
    ai_content_path = PROJECT_DIR / "ai_content.json"
    if ai_content_path.exists():
        try:
            with open(ai_content_path, 'r', encoding='utf-8') as f:
                ai_data = json.load(f)
            
            # 检查emoji前缀
            emoji_prefix_count = 0
            template_count = 0
            truncate_count = 0
            
            for key, content in ai_data.items():
                zh = content.get('zh', '')
                persp = content.get('persp', '')
                action = content.get('action', '')
                
                # emoji前缀检查
                if emoji_pattern.match(zh) or emoji_pattern.match(persp) or emoji_pattern.match(action):
                    emoji_prefix_count += 1
                
                # 模板内容检查
                all_text = f"{zh} {persp} {action}"
                if 'AI行业持续快速演进' in all_text or '深入了解该内容' in all_text:
                    template_count += 1
                
                # 截断检查
                if zh.endswith('...') and len(zh) < 50:
                    truncate_count += 1
            
            quality_issues = []
            if emoji_prefix_count > 0:
                quality_issues.append(f"{emoji_prefix_count}条含emoji前缀")
            if template_count > 0:
                quality_issues.append(f"{template_count}条使用模板内容")
            if truncate_count > 0:
                quality_issues.append(f"{truncate_count}条内容截断")
            
            if quality_issues:
                issues.append(f"AI内容质量问题: {', '.join(quality_issues)}，运行 python ai_content_validator.py fix")
                logger.warning(f"  ❌ 发现问题: {', '.join(quality_issues)}")
            else:
                logger.info(f"  ✅ AI内容质量合格 ({len(ai_data)}条)")
        except Exception as e:
            warnings.append(f"AI内容检查失败: {e}")
            logger.warning(f"  ⚠️ 检查失败: {e}")
    else:
        logger.info("  ⚠️ ai_content.json不存在，跳过检查")
    pbar.update(1)

    # --- 12. AI空容器检查 ---
    logger.info("\n🕳️ AI空容器检查:")
    ai_containers = re.findall(r'<div class="ai-insights">(.*?)</div>', html, re.DOTALL)
    empty_containers = [c for c in ai_containers if not c.strip()]
    total_cards = len(re.findall(r'class="topic-card"', html))
    filled_containers = len(ai_containers) - len(empty_containers)
    if total_cards > 0:
        coverage_rate = filled_containers / total_cards * 100
        logger.info(f"  总卡片: {total_cards}, 有AI内容: {filled_containers}, 空容器: {len(empty_containers)}")
        logger.info(f"  AI实际覆盖率: {coverage_rate:.1f}% (有内容的容器/总卡片)")
        if len(empty_containers) > 0:
            issues.append(f"发现 {len(empty_containers)} 个空AI容器（有边框无内容），检查generate_v3.7.py的_build_ai_insights()")
            logger.warning(f"  ❌ 空容器: {empty_containers} 个（应通过_build_ai_insights()避免生成）")
        elif coverage_rate < 100:
            warnings.append(f"AI覆盖率仅 {coverage_rate:.1f}%，{total_cards - filled_containers} 张卡片无AI内容（需配置API Key生成）")
            logger.info(f"  ⚠️ {total_cards - filled_containers} 张卡片无AI小结（非空容器问题，是内容缺失）")
        else:
            logger.info(f"  ✅ 所有卡片均有AI内容，无空容器")
    else:
        logger.info("  ⚠️ 无内容卡片，跳过检查")
    pbar.update(1)

    # --- 13. GitHub Pages 部署验证 ---
    logger.info("\n🚀 GitHub Pages 部署验证:")
    try:
        import urllib.request
        deployed_url = "https://afuxh.github.io/fuqing-profile/hot-news.html"
        req = urllib.request.Request(deployed_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            deployed_html = resp.read().decode('utf-8', errors='ignore')
            deployed_len = len(deployed_html)
            has_ai_insights = 'class="ai-insight-item' in deployed_html or 'perspective-comment' in deployed_html
            has_value_tags = 'class="value-tag' in deployed_html or 'value-tags' in deployed_html
            local_len = len(html)
            logger.info(f"  本地文件: {local_len:,} 字节")
            logger.info(f"  GitHub Pages: {deployed_len:,} 字节")
            ai_status = "✅ 存在" if has_ai_insights else "❌ 缺失"
            tag_status = "✅ 存在" if has_value_tags else "❌ 缺失"
            logger.info(f"  AI内容: {ai_status}")
            logger.info(f"  价值标签: {tag_status}")
            if deployed_len < 100000:
                issues.append(f"GitHub Pages部署文件异常小({deployed_len}字节)，可能未更新")
            elif not has_ai_insights:
                issues.append("GitHub Pages部署版本缺少AI内容，CI/CD可能失败或未触发")
            elif not has_value_tags:
                issues.append("GitHub Pages部署版本缺少价值标签，generate脚本可能异常")
            else:
                logger.info("  ✅ 部署验证通过")
    except Exception as e:
        warnings.append(f"GitHub Pages验证失败: {e}（可能网络问题，不影响本地生成）")
    pbar.update(1)

    elapsed = time.time() - start_time
    
    # --- 汇总 ---
    logger.info("\n" + "=" * 50)
    if issues:
        logger.error(f"❌ 严重问题 ({len(issues)}):")
        for i in issues:
            logger.error(f"  - {i}")
    
    if warnings:
        logger.warning(f"⚠️ 警告 ({len(warnings)}):")
        for w in warnings:
            logger.warning(f"  - {w}")
    
    if not issues and not warnings:
        logger.info(f"✅ 所有检查通过！（用时 {ProgressBar._fmt_time(elapsed)}）")
    elif not issues:
        logger.info(f"✅ 基本检查通过（有警告，不影响使用）（用时 {ProgressBar._fmt_time(elapsed)}）")
    
    return {"issues": issues, "warnings": warnings, "card_count": card_count}


# ============ diagnose: 环境诊断 ============
def diagnose():
    """诊断运行环境"""
    logger.info("=" * 50)
    logger.info("  🩺 环境诊断")
    logger.info("=" * 50)
    
    diag_items = 8  # Python/依赖/网络/Git/部署/Actions/日志/新功能
    pbar = ProgressBar(diag_items, "🩺 环境诊断")
    start_time = time.time()
    
    results = {}
    
    # --- Python 版本 ---
    logger.info("\n🐍 Python 环境:")
    py_ver = sys.version
    logger.info(f"  版本: {py_ver.split()[0]}")
    results["python_version"] = py_ver
    
    # Python 3.11 兼容性警告
    major, minor = sys.version_info[:2]
    if (major, minor) == (3, 11):
        logger.info("  ⚠️ Python 3.11: 注意 f-string 中不能使用 \\U 转义和函数调用")
        logger.info("     脚本已做兼容处理（预计算变量 + 实际 emoji 字符）")
    
    pbar.update(1)
    
    # --- 依赖检查 ---
    logger.info("\n📦 依赖检查:")
    deps = {
        "httpx": "数据请求",
        "yaml": "配置解析",
        "deep_translator": "翻译（可选）",
    }
    for pkg, desc in deps.items():
        try:
            __import__(pkg)
            logger.info(f"  ✅ {pkg} ({desc})")
        except ImportError:
            logger.warning(f"  ❌ {pkg} ({desc}) - 未安装")
            results[f"missing_{pkg}"] = True
    
    pbar.update(1)
    
    # --- 网络可达性 ---
    logger.info("\n🌐 API 可达性（预计5-10秒）:")
    endpoints = {
        "Hacker News": "https://hacker-news.firebaseio.com/v0/topstories.json",
        "GitHub API": "https://api.github.com",
        "Product Hunt": "https://www.producthunt.com",
        "36氪 RSS": "https://36kr.com/feed",
        "V2EX API": "https://www.v2ex.com/api/topics/hot.json",
    }
    
    import urllib.request
    for name, url in endpoints.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OpsTool/1.0"})
            urllib.request.urlopen(req, timeout=10)
            logger.info(f"  ✅ {name}")
            results[f"reachable_{name}"] = True
        except Exception as e:
            logger.warning(f"  ❌ {name}: {str(e)[:60]}")
            results[f"reachable_{name}"] = False
    
    pbar.update(1)
    
    # --- Git 状态 ---
    logger.info("\n📂 Git 状态:")
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True, text=True, timeout=5, cwd=PROJECT_DIR
        )
        if result.stdout.strip():
            logger.info(f"  远程仓库:")
            for line in result.stdout.strip().split("\n"):
                logger.info(f"    {line}")
        else:
            logger.info("  无远程仓库")
        
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=5, cwd=PROJECT_DIR
        )
        if result.stdout.strip():
            logger.info(f"  未提交更改:")
            for line in result.stdout.strip().split("\n")[:10]:
                logger.info(f"    {line}")
        else:
            logger.info("  ✅ 工作区干净")
    except FileNotFoundError:
        logger.info("  Git 未安装")
    except Exception as e:
        logger.warning(f"  Git 检查失败: {e}")
    
    pbar.update(1)
    
    # 检测是否有部署工具
    deploy_tool = PROJECT_DIR / "github_deploy.py"
    if deploy_tool.exists():
        logger.info(f"\n🚀 GitHub部署工具: python github_deploy.py")
        logger.info(f"  环境检测: python github_deploy.py check")
        logger.info(f"  一键部署: python github_deploy.py quick")
        logger.info(f"  详细诊断: python github_deploy.py diagnose")
    
    pbar.update(1)
    
    # --- GitHub Actions 配置 ---
    logger.info("\n⚙️ GitHub Actions:")
    workflow = PROJECT_DIR / ".github" / "workflows" / "auto-generate.yml"
    if workflow.exists():
        logger.info(f"  ✅ 工作流文件存在: {workflow}")
        content = workflow.read_text(encoding="utf-8")
        if "python" in content.lower():
            logger.info("  包含 Python 运行步骤")
        if "setup-python" in content:
            logger.info("  使用 setup-python action")
    else:
        logger.info("  ⚠️ 无 GitHub Actions 工作流")
    
    pbar.update(1)
    
    # --- 日志分析 ---
    logger.info("\n📝 最近运行日志:")
    log_files = sorted(
        PROJECT_DIR.glob("*.log"),
        key=lambda x: x.stat().st_mtime, reverse=True
    )
    if log_files:
        for lf in log_files[:3]:
            mtime = datetime.fromtimestamp(lf.stat().st_mtime, tz=CST)
            logger.info(f"  {lf.name} ({mtime.strftime('%Y-%m-%d %H:%M')})")
    else:
        logger.info("  无日志文件")
    
    pbar.update(1)
    
    # --- 8. 新功能检查 ---
    logger.info("\n🆕 新功能检查:")
    
    # 检查 trend_analyzer.py
    trend_analyzer = PROJECT_DIR / "trend_analyzer.py"
    if trend_analyzer.exists():
        logger.info("  ✅ trend_analyzer.py 存在")
        results["trend_analyzer"] = True
    else:
        logger.warning("  ❌ trend_analyzer.py 不存在")
        results["trend_analyzer"] = False
    
    # 检查 sites.yaml (多站点配置)
    sites_yaml = PROJECT_DIR / "sites.yaml"
    if sites_yaml.exists():
        logger.info("  ✅ sites.yaml 存在（多站点配置）")
        results["sites_yaml"] = True
        try:
            import yaml
            with open(sites_yaml, 'r', encoding='utf-8') as f:
                sites_config = yaml.safe_load(f)
            if isinstance(sites_config, dict) and 'sites' in sites_config:
                site_count = len(sites_config['sites'])
                logger.info(f"     配置站点数: {site_count}")
                results["site_count"] = site_count
        except Exception as e:
            logger.warning(f"     解析失败: {e}")
    else:
        logger.info("  ⚠️ sites.yaml 不存在（使用单站点模式）")
        results["sites_yaml"] = False
    
    # 检查数据源数量（从 config.yaml）
    try:
        config = _load_config()
        sources = config.get("sources", [])
        source_count = len(sources)
        results["source_count"] = source_count
        if source_count >= 20:
            logger.info(f"  ✅ 数据源数量: {source_count} (v3.9 已扩展到25)")
        else:
            logger.info(f"  ⚠️ 数据源数量: {source_count} (建议升级到20+)")
    except Exception:
        logger.warning("  ⚠️ 无法读取数据源配置")
    
    pbar.update(1)
    
    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 50}")
    logger.info(f"  诊断完成（用时 {ProgressBar._fmt_time(elapsed)}）")
    return results


# ============ fix: 常见问题自动修复 ============
def fix():
    """自动修复常见问题"""
    logger.info("=" * 50)
    logger.info("  🔧 自动修复")
    logger.info("=" * 50)
    
    fix_items = 4  # site目录/兼容性/依赖/gitignore
    pbar = ProgressBar(fix_items, "🔧 自动修复")
    start_time = time.time()
    
    fixed = []
    skipped = []
    
    # --- 1. 检查 site 目录 ---
    site_dir = PROJECT_DIR / "site"
    if not site_dir.exists():
        site_dir.mkdir(parents=True)
        fixed.append("创建 site/ 目录")
        logger.info("✅ 创建 site/ 目录")
    
    pbar.update(1)
    
    # --- 2. 检查 Python 版本兼容性标记 ---
    gen_file = PROJECT_DIR / "generate_v3.7.py"
    if gen_file.exists():
        content = gen_file.read_text(encoding="utf-8")
        
        # 检查是否有未处理的 \\U 转义
        u_escapes = re.findall(r'\\\\U[0-9a-fA-F]{8}', content)
        if u_escapes:
            fixed.append(f"警告: 脚本中仍存在 {len(u_escapes)} 个 \\U 转义")
            logger.warning(f"⚠️ 发现 {len(u_escapes)} 个未处理的 \\U 转义（Python 3.11 可能报错）")
    
    pbar.update(1)
    
    # --- 3. 检查依赖 ---
    try:
        __import__("httpx")
    except ImportError:
        logger.info("正在安装 httpx...")
        subprocess.run([sys.executable, "-m", "pip", "install", "httpx"], check=False)
        fixed.append("安装 httpx")
    
    try:
        __import__("yaml")
    except ImportError:
        logger.info("正在安装 pyyaml...")
        subprocess.run([sys.executable, "-m", "pip", "install", "pyyaml"], check=False)
        fixed.append("安装 pyyaml")
    
    pbar.update(1)
    
    # --- 4. 检查 .gitignore ---
    gitignore = PROJECT_DIR / ".gitignore"
    needed = ["site/", "*.log", "__pycache__/", ".DS_Store"]
    if not gitignore.exists():
        gitignore.write_text("\n".join(needed) + "\n", encoding="utf-8")
        fixed.append("创建 .gitignore")
        logger.info("✅ 创建 .gitignore")
    else:
        content = gitignore.read_text(encoding="utf-8")
        for item in needed:
            if item not in content:
                with open(gitignore, "a", encoding="utf-8") as f:
                    f.write(f"{item}\n")
                fixed.append(f"添加 {item} 到 .gitignore")
    
    pbar.update(1)
    
    elapsed = time.time() - start_time
    
    # --- 汇总 ---
    logger.info("\n" + "=" * 50)
    if fixed:
        logger.info(f"✅ 已修复 {len(fixed)} 项（用时 {ProgressBar._fmt_time(elapsed)}）:")
        for f in fixed:
            logger.info(f"  - {f}")
    else:
        logger.info(f"✅ 无需修复，一切正常（用时 {ProgressBar._fmt_time(elapsed)}）")
    
    return fixed


# ============ history: 运行历史分析 ============
def history():
    """分析运行历史日志"""
    logger.info("=" * 50)
    logger.info("  📊 运行历史分析")
    logger.info("=" * 50)
    
    log_files = sorted(
        PROJECT_DIR.glob("*.log"),
        key=lambda x: x.stat().st_mtime, reverse=True
    )
    
    if not log_files:
        logger.info("无日志文件")
        return
    
    runs = []
    for lf in log_files:
        content = lf.read_text(encoding="utf-8", errors="replace")
        
        # 提取运行信息
        run_time = datetime.fromtimestamp(lf.stat().st_mtime, tz=CST)
        
        # 抓取数量
        fetch_match = re.search(r'共抓取 (\d+) 条', content)
        fetch_count = int(fetch_match.group(1)) if fetch_match else 0
        
        # 展示数量
        display_match = re.search(r'展示: (\d+) 篇', content)
        display_count = int(display_match.group(1)) if display_match else 0
        
        # 错误数
        error_count = len(re.findall(r'\[ERROR\]', content))
        
        # 翻译状态
        translated = "跳过翻译" not in content and "翻译" in content
        
        runs.append({
            "time": run_time,
            "file": lf.name,
            "fetched": fetch_count,
            "displayed": display_count,
            "errors": error_count,
            "translated": translated,
        })
    
    # 统计
    logger.info(f"\n总运行次数: {len(runs)}")
    
    if runs:
        logger.info(f"最近运行: {runs[0]['time'].strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"  - 抓取: {runs[0]['fetched']} 条")
        logger.info(f"  - 展示: {runs[0]['displayed']} 篇")
        logger.info(f"  - 错误: {runs[0]['errors']}")
        logger.info(f"  - 翻译: {'是' if runs[0]['translated'] else '否'}")
    
    # 异常率趋势
    error_runs = [r for r in runs if r["errors"] > 0]
    if error_runs:
        logger.info(f"\n⚠️ 异常运行: {len(error_runs)}/{len(runs)}")
        for r in error_runs:
            logger.info(f"  {r['time'].strftime('%m-%d %H:%M')}: {r['errors']} 个错误")
    
    return runs


# ============ quick: 快速运维 ============
def quick_ops():
    """快速运维：诊断 + 检查 + 建议"""
    logger.info("🚀 快速运维模式\n")
    start_time = time.time()
    
    pbar = ProgressBar(3, "🚀 快速运维")
    
    diag = diagnose()
    pbar.update(1)
    
    logger.info("\n")
    chk = check()
    pbar.update(1)
    
    # 综合建议
    logger.info("\n💡 综合建议:")
    suggestions = []
    
    if chk.get("issues"):
        suggestions.append("存在严重问题，建议先运行 `python ops_tool.py fix` 自动修复")
    
    for w in chk.get("warnings", []):
        if "翻译" in w:
            suggestions.append("翻译覆盖率低：检查 translate.enabled 配置和网络")
        if "无高分" in w:
            suggestions.append("无高分内容：调整 scoring.keywords.core 关键词或降低阈值")
    
    if diag.get("missing_deep_translator"):
        suggestions.append("翻译功能不可用：运行 pip install deep-translator")
    
    # 新功能建议
    if not diag.get("trend_analyzer"):
        suggestions.append("趋势分析器缺失：添加 trend_analyzer.py 以启用趋势分析功能")
    
    if not diag.get("sites_yaml"):
        suggestions.append("多站点配置缺失：添加 sites.yaml 以支持多站点部署")
    
    source_count = diag.get("source_count", 0)
    if source_count > 0 and source_count < 20:
        suggestions.append(f"数据源数量({source_count})较少：建议升级到20+个数据源以获取更全面的热点")
    
    if not suggestions:
        suggestions.append("一切正常，无需操作")
    
    for s in suggestions:
        logger.info(f"  • {s}")
    
    pbar.update(1)
    
    elapsed = time.time() - start_time
    logger.info(f"\n  总用时: {ProgressBar._fmt_time(elapsed)}")


# ============ git: Git健康检查 ============
def git_check():
    """检查Git仓库健康状态"""
    logger.info("=" * 50)
    logger.info(f"  🔧 Git仓库健康检查 (v{OPS_TOOL_VERSION})")
    logger.info("=" * 50)
    
    try:
        from git_helper import check_git_health, setup_git_config, repair_git_repo
    except ImportError:
        logger.error("❌ git_helper.py 不存在，无法执行Git检查")
        return
    
    healthy, checks = check_git_health(PROJECT_DIR)
    
    logger.info("\n📋 检查结果:")
    for k, v in checks.items():
        status = "✅" if v else "❌"
        logger.info(f"  {status} {k}")
    
    if healthy:
        logger.info("\n✅ Git仓库健康")
    else:
        logger.warning("\n⚠️ Git仓库存在问题")
        logger.info("\n💡 建议:")
        logger.info("  1. 运行 `python ops_tool.py git-setup` 配置Git")
        logger.info("  2. 运行 `python ops_tool.py git-repair` 修复仓库")
    
    return healthy


def git_setup():
    """配置Git避免Windows问题"""
    logger.info("=" * 50)
    logger.info("  ⚙️ Git配置优化")
    logger.info("=" * 50)
    
    try:
        from git_helper import setup_git_config
        setup_git_config()
        logger.info("\n✅ Git配置完成")
    except ImportError:
        logger.error("❌ git_helper.py 不存在")


def git_repair():
    """修复损坏的Git仓库"""
    logger.info("=" * 50)
    logger.info("  🔧 Git仓库修复")
    logger.info("=" * 50)
    
    try:
        from git_helper import repair_git_repo
        # 尝试读取remote URL
        remote_url = None
        try:
            result = subprocess.run(
                ['git', 'config', '--get', 'remote.origin.url'],
                cwd=PROJECT_DIR,
                capture_output=True, text=True
            )
            if result.returncode == 0:
                remote_url = result.stdout.strip()
        except:
            pass
        
        if not remote_url:
            remote_url = input("请输入远程仓库URL (如 https://github.com/user/repo.git): ").strip()
        
        if repair_git_repo(PROJECT_DIR, remote_url):
            logger.info("\n✅ 修复成功")
        else:
            logger.error("\n❌ 修复失败")
    except ImportError:
        logger.error("❌ git_helper.py 不存在")


# ============ version: 显示版本信息 ============
def show_version():
    """显示版本信息"""
    logger.info("=" * 50)
    logger.info("  📋 版本信息")
    logger.info("=" * 50)
    logger.info(f"\n运维工具版本: v{OPS_TOOL_VERSION}")
    
    try:
        from version import __version__ as proj_ver, VERSION_HISTORY
        logger.info(f"项目版本: v{proj_ver}")
        
        if VERSION_HISTORY:
            logger.info("\n📜 版本历史:")
            for ver, date, desc in VERSION_HISTORY[:5]:
                logger.info(f"  v{ver} ({date}): {desc}")
    except ImportError:
        logger.info("项目版本: 未知 (version.py不存在)")


# ============ CLI ============
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=f"热点聚合网站运维工具 v{OPS_TOOL_VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
示例:
  python ops_tool.py check       # 检查HTML完整性
  python ops_tool.py diagnose    # 诊断运行环境
  python ops_tool.py fix         # 自动修复常见问题
  python ops_tool.py history     # 分析运行历史
  python ops_tool.py quick       # 快速运维（推荐）
  python ops_tool.py git         # Git健康检查
  python ops_tool.py git-setup   # 配置Git
  python ops_tool.py git-repair  # 修复Git仓库
  python ops_tool.py version     # 显示版本信息

版本: v{OPS_TOOL_VERSION}
        """
    )
    parser.add_argument("action", nargs="?", default="quick",
                        choices=["check", "diagnose", "fix", "history", "quick", 
                                "git", "git-setup", "git-repair", "version"],
                        help="运维操作 (默认: quick)")
    args = parser.parse_args()
    
    actions = {
        "check": check,
        "diagnose": diagnose,
        "fix": fix,
        "history": history,
        "quick": quick_ops,
        "git": git_check,
        "git-setup": git_setup,
        "git-repair": git_repair,
        "version": show_version,
    }
    
    actions[args.action]()
