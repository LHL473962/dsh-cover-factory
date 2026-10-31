#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 RVC 2.2.231006 的兼容补丁覆盖到已 clone 的 RVC 根目录。

用法：python engine/vendor/rvc_patches/apply.py <rvc_root>
幂等：只覆盖补丁文件；首次覆盖前把目标备份为 <file>.orig（已存在则不重复备份）。
"""
import argparse
import filecmp
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PATCH_VERSION = "2.2.231006+torch26+pyav+indexfix"
SKIP_NAMES = {"apply.py", "README.md", "LICENSE", "PATCHES.json"}
SKIP_SUFFIX = (".orig", ".pyc")


def patch_files():
    out = []
    for cur, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIX):
                continue
            src = os.path.join(cur, name)
            rel = os.path.relpath(src, HERE)
            out.append((rel, src))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="apply dsh-cover-factory RVC compatibility patches")
    parser.add_argument("rvc_root", help="cloned RVC 2.2.231006 root")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.rvc_root)
    if not os.path.isfile(os.path.join(root, "tools", "infer_cli.py")):
        print("ERROR: not an RVC root (tools/infer_cli.py missing): " + root)
        return 2

    changed, skipped = [], []
    for rel, src in patch_files():
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            same = os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False)
        except OSError:
            same = False
        if not same:
            if os.path.isfile(dst):
                backup = dst + ".orig"
                if not os.path.isfile(backup):
                    shutil.copy2(dst, backup)
            shutil.copy2(src, dst)
            changed.append(rel)
        else:
            skipped.append(rel)

    marker = {
        "patch": PATCH_VERSION,
        "upstream_tag": "2.2.231006",
        "changed": changed,
        "alreadyUpToDate": skipped,
    }
    try:
        with open(os.path.join(root, ".dsh_rvc_patches.json"), "w", encoding="utf-8") as f:
            json.dump(marker, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    print("RVC patches %s: changed=%d skipped=%d root=%s" % (
        PATCH_VERSION, len(changed), len(skipped), root))
    for rel in changed:
        print("  patched " + rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
