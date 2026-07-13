"""
AI内容质量保障工具
==================
自动检测和修复AI内容异常问题：
1. emoji重复（zh/persp/action字段含emoji前缀）
2. 模板化内容（通用模板而非针对性内容）
3. JSON格式错误（中文引号等）
4. 内容截断（只是标题截断+...）

用法：
  python ai_content_validator.py check    # 检查问题
  python ai_content_validator.py fix      # 自动修复
  python ai_content_validator.py strict   # 严格模式（修复+验证）
"""
import json
import re
import sys
from pathlib import Path

# emoji模式
EMOJI_PATTERN = re.compile(r'^[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF]\s*')

# 通用模板内容（需要替换为针对性内容）
TEMPLATE_PATTERNS = [
    r'AI行业持续快速演进',
    r'深入了解该内容',
    r'评估其对你的工作或学习是否有参考价值',
    r'AI Agent领域持续创新',
    r'体验新的Agent工具',
    r'思考如何将Agent集成到你的工作流中',
    r'新AI模型发布，推动模型能力边界持续扩展',
    r'模型迭代速度加快',
    r'评估新模型是否适合你的使用场景',
    r'关注其成本效益比',
    r'NVIDIA发布最新AI基础设施和工具',
    r'NVIDIA正在从芯片公司转型为AI全栈平台',
    r'关注NVIDIA新产品的技术细节和定价策略',
    r'资本市场动态持续活跃',
    r'关注相关赛道的投资趋势和商业化进展',
]

FIELD_ALIASES = {
    'summary': ('zh_summary', 'zh', 'summary'),
    'perspective': ('perspective', 'persp', 'perspective_comment'),
    'action': ('action', 'guidance', 'action_guidance'),
}


def get_field(content, logical_name):
    for key in FIELD_ALIASES[logical_name]:
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''

# 中文引号替换
QUOTE_REPLACEMENTS = {
    '"': '"',  # 中文左引号 -> 英文引号
    '"': '"',  # 中文右引号 -> 英文引号
    ''': "'",  # 中文左单引号
    ''': "'",  # 中文右单引号
}


def load_ai_content(path='ai_content.json'):
    """加载AI内容JSON"""
    file_path = Path(path)
    if not file_path.exists():
        return None, f"文件不存在: {path}"
    
    try:
        content = file_path.read_text(encoding='utf-8')
        # 预处理：替换中文引号
        for cn, en in QUOTE_REPLACEMENTS.items():
            content = content.replace(cn, en)
        data = json.loads(content)
        return data, None
    except json.JSONDecodeError as e:
        return None, f"JSON解析错误: {e}"
    except Exception as e:
        return None, f"读取错误: {e}"


def save_ai_content(data, path='ai_content.json'):
    """保存AI内容JSON"""
    file_path = Path(path)
    try:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        file_path.write_text(content, encoding='utf-8')
        return True, None
    except Exception as e:
        return False, str(e)


def check_entry(key, content):
    """检查单条AI内容"""
    issues = []
    
    zh = get_field(content, 'summary')
    persp = get_field(content, 'perspective')
    action = get_field(content, 'action')
    
    # 1. 检查emoji前缀
    if EMOJI_PATTERN.match(zh):
        issues.append(('emoji_zh', 'zh字段含emoji前缀'))
    if EMOJI_PATTERN.match(persp):
        issues.append(('emoji_persp', 'persp字段含emoji前缀'))
    if EMOJI_PATTERN.match(action):
        issues.append(('emoji_action', 'action字段含emoji前缀'))
    
    # 2. 检查模板化内容
    all_text = f"{zh} {persp} {action}"
    for pattern in TEMPLATE_PATTERNS:
        if re.search(pattern, all_text):
            issues.append(('template', f'使用模板内容: {pattern[:20]}...'))
            break  # 只报告一次
    
    # 3. 检查内容截断
    if zh.endswith('...') and len(zh) < 50:
        issues.append(('truncate', 'zh字段可能只是标题截断'))
    
    # 4. 检查内容过短
    if len(zh) < 20:
        issues.append(('short', 'zh字段内容过短'))
    
    return issues


def fix_entry(key, content):
    """修复单条AI内容"""
    fixed = content.copy()
    changes = []
    
    # 1. 移除emoji前缀
    for field in ('zh_summary', 'zh', 'summary', 'perspective', 'persp', 'perspective_comment', 'action', 'guidance', 'action_guidance'):
        if field in fixed:
            original = fixed[field]
            cleaned = EMOJI_PATTERN.sub('', original).strip()
            if cleaned != original:
                fixed[field] = cleaned
                changes.append(f'{field}: 移除emoji前缀')
    
    return fixed, changes


def check_all(data):
    """检查所有AI内容"""
    results = {
        'total': len(data),
        'issues_count': 0,
        'issues': []
    }
    
    for key, content in data.items():
        issues = check_entry(key, content)
        if issues:
            results['issues_count'] += 1
            results['issues'].append({
                'key': key[:50] + '...' if len(key) > 50 else key,
                'issues': issues
            })
    
    return results


def fix_all(data, auto_fix_template=False):
    """修复所有AI内容"""
    results = {
        'fixed_count': 0,
        'changes': []
    }
    
    for key, content in data.items():
        fixed, changes = fix_entry(key, content)
        if changes:
            data[key] = fixed
            results['fixed_count'] += 1
            results['changes'].append({
                'key': key[:50] + '...' if len(key) > 50 else key,
                'changes': changes
            })
    
    return results


def validate():
    """验证AI内容质量"""
    print("=" * 60)
    print("  🔍 AI内容质量检查")
    print("=" * 60)
    
    data, error = load_ai_content()
    if error:
        print(f"\n❌ 加载失败: {error}")
        return False
    
    print(f"\n📊 加载 {len(data)} 条AI内容")
    
    results = check_all(data)
    
    if results['issues_count'] == 0:
        print("\n✅ 所有内容质量合格")
        return True
    
    print(f"\n⚠️ 发现 {results['issues_count']} 条有问题:")
    
    # 按问题类型统计
    issue_types = {}
    for item in results['issues']:
        for issue_type, _ in item['issues']:
            issue_types[issue_type] = issue_types.get(issue_type, 0) + 1
    
    print("\n问题类型统计:")
    for itype, count in sorted(issue_types.items(), key=lambda x: -x[1]):
        print(f"  - {itype}: {count} 条")
    
    print(f"\n详细问题 (前10条):")
    for item in results['issues'][:10]:
        print(f"\n  📌 {item['key']}")
        for issue_type, desc in item['issues']:
            print(f"    ❌ [{issue_type}] {desc}")
    
    if len(results['issues']) > 10:
        print(f"\n  ... 还有 {len(results['issues']) - 10} 条问题")
    
    return False


def fix():
    """自动修复AI内容"""
    print("=" * 60)
    print("  🔧 AI内容自动修复")
    print("=" * 60)
    
    data, error = load_ai_content()
    if error:
        print(f"\n❌ 加载失败: {error}")
        return False
    
    print(f"\n📊 加载 {len(data)} 条AI内容")
    
    # 先检查
    check_results = check_all(data)
    if check_results['issues_count'] == 0:
        print("\n✅ 无需修复，所有内容质量合格")
        return True
    
    print(f"\n⚠️ 发现 {check_results['issues_count']} 条有问题，开始修复...")
    
    # 修复
    fix_results = fix_all(data)
    
    print(f"\n✅ 已修复 {fix_results['fixed_count']} 条:")
    for item in fix_results['changes'][:10]:
        print(f"\n  📌 {item['key']}")
        for change in item['changes']:
            print(f"    ✓ {change}")
    
    # 保存
    success, error = save_ai_content(data)
    if success:
        print(f"\n✅ 已保存到 ai_content.json")
    else:
        print(f"\n❌ 保存失败: {error}")
        return False
    
    # 再次验证
    print("\n" + "=" * 60)
    print("  🔍 修复后验证")
    print("=" * 60)
    
    check_after = check_all(data)
    if check_after['issues_count'] == 0:
        print("\n✅ 验证通过，所有问题已修复")
        return True
    else:
        print(f"\n⚠️ 仍有 {check_after['issues_count']} 条问题需要手动处理")
        print("   （模板化/过短内容，不影响部署）")
        return False


def strict():
    """严格模式：只验证不改写，任何问题都阻断发布。"""
    print("=" * 60)
    print("  🛡️ AI内容严格质量保障")
    print("=" * 60)
    
    return validate()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='AI内容质量保障工具')
    parser.add_argument('action', nargs='?', default='check',
                       choices=['check', 'fix', 'strict'],
                       help='操作: check(检查) fix(修复) strict(严格模式)')
    
    args = parser.parse_args()
    
    if args.action == 'check':
        success = validate()
    elif args.action == 'fix':
        success = fix()
    elif args.action == 'strict':
        success = strict()
    
    sys.exit(0 if success else 1)
