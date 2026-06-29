"""
chat-report HTML 渲染器 v1.0
阶段四：Jinja2 模板 + 数据注入 → index.html

用法：
    python render.py                              # 渲染到 output/chat-stats-pipeline/index.html
    python render.py --deploy                     # 渲染并复制到部署路径

产出：
    output/chat-stats-pipeline/index.html
"""

import json
import os
import sys
import shutil
from datetime import datetime

# Try Jinja2, fall back to simple string replacement
try:
    from jinja2 import Template
    HAS_JINJA2 = True
except ImportError:
    HAS_JINJA2 = False
    print("[WARN] Jinja2 未安装，使用简单模板引擎。安装: pip install jinja2")


# ============================================================
# 配置
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = SCRIPT_DIR

TEMPLATE_PATH = os.path.join(OUTPUT_DIR, "template.html")
STATS_PATH = os.path.join(OUTPUT_DIR, "chat_stats.json")
ANALYSIS_PATH = os.path.join(OUTPUT_DIR, "chat_analysis.json")
HTML_OUTPUT = os.path.join(OUTPUT_DIR, "index.html")

DEPLOY_PATH = r"D:\工作项目\gh-pages-deploy\chat-report\index.html"


def simple_render(template_text, context):
    """简易模板引擎：{{ var }} 和 {% for %} 替换"""
    import re
    
    result = template_text
    
    # Handle {{ var.subvar|filter }}
    def replace_var(match):
        expr = match.group(1).strip()
        
        # Handle filters: var|filter
        parts = expr.split('|')
        var_path = parts[0].strip()
        filters = [p.strip() for p in parts[1:]]
        
        # Resolve dotted path
        value = context
        for key in var_path.split('.'):
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and key.isdigit():
                value = value[int(key)]
            else:
                value = ""
                break
        
        # Apply filters
        for f in filters:
            if f == 'length':
                value = len(value) if value else 0
            elif f == 'max':
                value = max(value) if value else 0
            elif f == 'int':
                value = int(value) if value else 0
            elif f.startswith('format('):
                fmt = f[7:-1]
                if fmt in ('"%02d"', "'%02d'"):
                    value = f"{int(value):02d}" if value is not None else "00"
        
        return str(value) if value is not None else ""
    
    # Handle {{ ... }}
    result = re.sub(r'\{\{\s*(.+?)\s*\}\}', replace_var, result)
    
    # Handle simple {% for x in y %} ... {% endfor %}
    def replace_for(match):
        loop_var = match.group(1).strip()
        iter_expr = match.group(2).strip()
        body = match.group(3)
        
        # Handle slice: y[:N]
        slice_match = re.match(r'(\S+)\[:(.+?)\]', iter_expr)
        if slice_match:
            iter_path = slice_match.group(1)
            slice_val = slice_match.group(2)
        else:
            iter_path = iter_expr
            slice_val = None
        
        # Resolve iterable
        value = context
        for key in iter_path.split('.'):
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and key.isdigit():
                value = value[int(key)]
            else:
                value = []
                break
        
        if not isinstance(value, list):
            return ""
        
        # Apply slice
        if slice_val:
            if slice_val.isdigit():
                value = value[:int(slice_val)]
        
        # Replace loop variables
        parts = []
        for i, item in enumerate(value):
            item_body = body
            # loop.index
            item_body = item_body.replace('loop.index', str(i + 1))
            item_body = item_body.replace('loop.index0', str(i))
            
            # item.xxx
            if isinstance(item, dict):
                for k, v in item.items():
                    item_body = re.sub(
                        rf'\b{re.escape(loop_var)}\.{re.escape(k)}\b',
                        str(v) if v is not None else "",
                        item_body
                    )
                    # Handle filters on loop var
                    item_body = re.sub(
                        rf'\b{re.escape(loop_var)}\.{re.escape(k)}\s*\|\s*length\b',
                        str(len(v)) if v else "0",
                        item_body
                    )
            elif isinstance(item, (str, int, float)):
                item_body = re.sub(rf'\b{re.escape(loop_var)}\b(?!\.)', str(item), item_body)
            
            parts.append(item_body)
        
        return ''.join(parts)
    
    # Handle {% for var in expr %} ... {% endfor %}
    result = re.sub(
        r'\{%\s*for\s+(\w+)\s+in\s+(.+?)\s*%\}(.*?)\{%\s*endfor\s*%\}',
        replace_for,
        result,
        flags=re.DOTALL
    )
    
    # Handle {% if var %} ... {% endif %}
    def replace_if(match):
        cond = match.group(1).strip()
        body = match.group(2)
        
        # Simple truthy check
        value = context
        for key in cond.split('.'):
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list):
                try:
                    key = int(key)
                    value = value[key]
                except:
                    value = None
                    break
            else:
                value = None
                break
        
        if value:
            # Also handle .attr access within if body
            return body
        return ""
    
    result = re.sub(
        r'\{%\s*if\s+(.+?)\s*%\}(.*?)\{%\s*endif\s*%\}',
        replace_if,
        result,
        flags=re.DOTALL
    )
    
    # Clean up remaining Jinja2 syntax
    result = re.sub(r'\{%[^%]*%\}', '', result)
    
    # Handle {# comments #}
    result = re.sub(r'\{#[^#]*#\}', '', result)
    
    return result


def render_html():
    """渲染 HTML"""
    
    # 加载数据
    print("[RENDER] 加载数据...")
    with open(STATS_PATH, 'r', encoding='utf-8') as f:
        stats = json.load(f)
    
    with open(ANALYSIS_PATH, 'r', encoding='utf-8') as f:
        analysis_data = json.load(f)
    
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        template_text = f.read()
    
    # 构建模板上下文
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    context = {
        "generated_at": generated_at,
        "meta": stats["meta"],
        "overview": stats["overview"],
        "members": stats["members"],
        "topics": stats["topics"],
        "highlights": stats["highlights"],
        "interaction": stats["interaction"],
        "time_dist": stats["time_dist"],
        "daily": stats["daily"],
        "student_work": stats["student_work"],
        "shulin": stats["shulin"],
        "insights": stats["insights"],
        "max_hour_count": max(h["count"] for h in stats["time_dist"]) if stats["time_dist"] else 1,
        "analysis": {
            "topics_summary": analysis_data.get("topics_summary", ""),
            "highlights_summary": analysis_data.get("highlights_summary", ""),
            "interaction_summary": analysis_data.get("interaction_summary", ""),
            "insights_summary": analysis_data.get("insights_summary", ""),
        },
    }
    
    # 渲染
    print("[RENDER] 渲染模板...")
    if HAS_JINJA2:
        # 注册自定义过滤器
        def format_number(n):
            return "{:,}".format(int(n)) if n is not None else "0"
        
        def max_filter(seq):
            return max(seq) if seq else 0
        
        def map_filter(seq, attr):
            return [item.get(attr, 0) for item in seq] if seq else []
        
        env = None
        template = Template(template_text)
        template.globals['format_number'] = format_number
        template.globals['max'] = max_filter
        
        html = template.render(**context)
    else:
        html = simple_render(template_text, context)
    
    # 写入
    with open(HTML_OUTPUT, 'w', encoding='utf-8') as f:
        f.write(html)
    
    html_size = len(html.encode('utf-8'))
    print(f"[RENDER] 输出: {HTML_OUTPUT} ({html_size:,} bytes)")
    
    return html


def deploy():
    """部署到 GitHub Pages 路径"""
    if not os.path.exists(HTML_OUTPUT):
        print(f"[DEPLOY] 源文件不存在: {HTML_OUTPUT}")
        return False
    
    deploy_dir = os.path.dirname(DEPLOY_PATH)
    os.makedirs(deploy_dir, exist_ok=True)
    
    shutil.copy2(HTML_OUTPUT, DEPLOY_PATH)
    print(f"[DEPLOY] 已复制到: {DEPLOY_PATH}")
    return True


def main():
    html = render_html()
    
    if "--deploy" in sys.argv:
        deploy()
    
    print(f"\n[RENDER] 完成! 可在浏览器中打开查看。")
    print(f"[RENDER] 文件: {HTML_OUTPUT}")


if __name__ == "__main__":
    main()
