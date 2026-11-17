#!/bin/bash
# 二改用的测试脚本：在装不上项目依赖的环境里，用替身库把 tests/ 逐个文件跑一遍。
#
# 用法（在仓库根目录）：
#   bash 二改/测试替身/run_tests.sh /tmp/结果.txt
#
# 结果文件每行是：一条失败/错误的测试名，或者「文件名 exit=码 汇总」。已排序，
# 可以直接和 二改/测试替身/基线-*.txt 做 diff：
#   diff 二改/测试替身/基线-2026-11-17.txt /tmp/结果.txt
#
# 每个文件限时 60 秒，6 个并行，全部跑完大约两三分钟。
# 真实依赖装得上的环境不要用这个，直接 pytest tests/。
set -u
out="${1:?用法: run_tests.sh 结果文件}"
here="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}"
: > "$out"
ls tests/test_*.py | xargs -P 6 -I{} bash -c '
  r=$(timeout 60 python3 -m pytest -p pytest_asyncio -q -p no:cacheprovider {} -o addopts="" 2>&1); c=$?
  echo "$r" | grep -E "^(FAILED|ERROR)" | sed "s/ - .*//"
  echo "{} exit=$c $(echo "$r" | tail -1 | sed "s/ in [0-9.]*s.*//")"
' >> "$out"
sort -o "$out" "$out"
echo "通过的文件：$(grep -c "exit=0" "$out") / $(grep -c "exit=" "$out")"
