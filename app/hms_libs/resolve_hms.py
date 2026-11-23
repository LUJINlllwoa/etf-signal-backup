# -*- coding: utf-8 -*-
"""递归解析华为 Maven 上某坐标的完整依赖树，输出需要下载的 aar 列表。"""
import urllib.request
import re
import sys

REPO = "https://developer.huawei.com/repo/"
seen = {}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")


def pom_url(g, a, v):
    return REPO + g.replace(".", "/") + "/" + a + "/" + v + "/" + a + "-" + v + ".pom"


def parse_deps(g, a, v, depth=0):
    key = (g, a, v)
    if key in seen:
        return
    seen[key] = True
    print("  " * depth + "%s:%s:%s" % (g, a, v))
    try:
        txt = fetch(pom_url(g, a, v))
    except Exception as e:
        print("  " * depth + "  [pom 读取失败] %s" % e)
        return
    # 每个 <dependency> 块
    for block in re.findall(r"<dependency>(.*?)</dependency>", txt, re.S):
        dg = re.search(r"<groupId>(.*?)</groupId>", block)
        da = re.search(r"<artifactId>(.*?)</artifactId>", block)
        dv = re.search(r"<version>(.*?)</version>", block)
        scope = re.search(r"<scope>(.*?)</scope>", block)
        optional = re.search(r"<optional>(.*?)</optional>", block)
        if not (dg and da and dv):
            continue
        if scope and scope.group(1) in ("test", "provided"):
            continue
        if optional and optional.group(1) == "true":
            continue
        parse_deps(dg.group(1), da.group(1), dv.group(1), depth + 1)


if __name__ == "__main__":
    parse_deps("com.huawei.hms", "push", "6.11.0.300")
    print("\n=== 全部坐标（去重） ===")
    for (g, a, v) in sorted(seen):
        print("%s:%s:%s" % (g, a, v))
