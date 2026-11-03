"""检查提交标题的基本格式，不执行 Git 操作。"""
import argparse
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("title", help="需要检查的提交标题")
    title = parser.parse_args().title

    # scope 可省略，! 表示破坏兼容性；摘要要求以英文字母开头且只含可打印 ASCII。
    pattern = r"(feat|fix|refactor|docs|test|chore|perf|build|ci)(\([a-z0-9][a-z0-9_-]*\))?!?: [A-Za-z][ -~]*"
    if title != title.strip() or not re.fullmatch(pattern, title):
        print("检查失败：请使用 type(scope): English summary 格式。")
        print("例如：fix(eval): Preserve prompt placeholders")
        return 1

    if len(title) > 72:
        print("提示：标题超过 72 个字符，建议精简。")
    print("检查通过：提交标题格式正确。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
