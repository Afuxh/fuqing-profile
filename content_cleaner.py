"""
内容清洗工具 - 自动去除广告/推广内容
"""
import json
import re

# 广告/推广内容正则表达式
AD_PATTERNS = [
    r'#欢迎关注[^#]+#',  # #欢迎关注...#
    r'欢迎关注[^，,。]+[,，。]',  # 欢迎关注...，
    r'官方微信公众号[:：][^，,。]+[,，。]?',  # 官方微信公众号：...
    r'更多精彩内容[^。]*为您奉上[。]?',  # 更多精彩内容...为您奉上
    r'微信号[:：][^，,。]+[,，。]?',  # 微信号：...
]

def clean_text(text):
    """清洗文本中的广告内容"""
    if not text:
        return text
    
    cleaned = text
    for pattern in AD_PATTERNS:
        cleaned = re.sub(pattern, '', cleaned)
    
    # 清理多余的标点
    cleaned = re.sub(r'[,，]{2,}', '，', cleaned)
    cleaned = re.sub(r'[。]{2,}', '。', cleaned)
    cleaned = re.sub(r'，[。]', '。', cleaned)
    
    return cleaned.strip()

def clean_data_file():
    """清洗数据文件"""
    with open('data/latest.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    items = data.get('topics', [])
    cleaned_count = 0
    
    for item in items:
        # 清洗摘要
        summary = item.get('summary', '')
        cleaned_summary = clean_text(summary)
        if cleaned_summary != summary:
            item['summary'] = cleaned_summary
            cleaned_count += 1
        
        # 清洗中文小结
        zh_summary = item.get('zh_summary', '')
        cleaned_zh = clean_text(zh_summary)
        if cleaned_zh != zh_summary:
            item['zh_summary'] = cleaned_zh
            cleaned_count += 1
    
    # 保存
    with open('data/latest.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 已清洗 {cleaned_count} 条内容的广告")
    return cleaned_count

def clean_html_file():
    """清洗HTML文件"""
    with open('site/hot-news.html', 'r', encoding='utf-8') as f:
        html = f.read()
    
    original_len = len(html)
    
    # 清洗广告内容
    for pattern in AD_PATTERNS:
        html = re.sub(pattern, '', html)
    
    # 保存
    with open('site/hot-news.html', 'w', encoding='utf-8') as f:
        f.write(html)
    
    cleaned_len = len(html)
    removed = original_len - cleaned_len
    
    print(f"✅ 已清洗HTML，移除 {removed} 字符的广告内容")
    return removed

def main():
    print("=" * 60)
    print("内容清洗工具 - 去除广告/推广")
    print("=" * 60)
    
    print("\n1. 清洗数据文件...")
    data_cleaned = clean_data_file()
    
    print("\n2. 清洗HTML文件...")
    html_removed = clean_html_file()
    
    print("\n" + "=" * 60)
    print("清洗完成")
    print("=" * 60)
    print(f"数据文件: 清洗 {data_cleaned} 条")
    print(f"HTML文件: 移除 {html_removed} 字符")

if __name__ == "__main__":
    main()
