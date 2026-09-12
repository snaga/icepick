#!/usr/bin/env python3
"""README に書かれたコマンドが実際のバイナリに存在するかを照合する。

使い方:
    python check_readme_sync.py <バイナリのパス> <README のパス>

バイナリの `agent-context` が出力するコマンドツリーを正として、README 中の
`<ツール名> <グループ> <サブコマンド>` という記述を突き合わせる。
実在しないコマンドが 1 つでも見つかれば終了コード 1 を返す。
"""

import json
import re
import subprocess
import sys


def load_command_tree(binary):
    """agent-context を実行し、{グループ名: {サブコマンド名, ...}} を返す。"""
    proc = subprocess.run([binary, "agent-context"], capture_output=True, text=True)
    if proc.returncode != 0:
        print("  ERROR agent-context の実行に失敗しました: %s" % proc.stderr.strip())
        sys.exit(2)

    ctx = json.loads(proc.stdout)
    tree = {}

    def walk(cmd, path):
        for sub in cmd.get("subcommands") or []:
            tree.setdefault(" ".join(path), set()).add(sub["name"])
            walk(sub, path + [sub["name"]])

    walk(ctx["root_command"], [])
    return ctx["cli"], tree


def find_bad_references(tool, tree, readme):
    """README 中の実在しないコマンド参照を列挙する。"""
    pattern = re.compile(
        re.escape(tool) + r"\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?"
    )
    top_level = tree.get("", set())
    bad = []

    with open(readme, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            for group, sub in pattern.findall(line):
                if group not in top_level:
                    bad.append((lineno, group, "", "存在しないコマンドグループ"))
                elif sub and sub not in tree.get(group, set()):
                    bad.append((lineno, group, sub, "存在しないサブコマンド"))
    return bad


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)

    binary, readme = sys.argv[1], sys.argv[2]
    tool, tree = load_command_tree(binary)
    bad = find_bad_references(tool, tree, readme)

    for lineno, group, sub, reason in bad:
        print("  NG  README.md:%-4d %s %s  -- %s" % (lineno, group, sub, reason))

    total = sum(len(v) for k, v in tree.items() if k)
    print("  照合対象: %d グループ / 実在サブコマンド %d 件" % (len(tree) - 1, total))

    if bad:
        print("  => README とソースコードに相違があります。")
        sys.exit(1)

    print("  => README とソースコードは一致しています。")


if __name__ == "__main__":
    main()
