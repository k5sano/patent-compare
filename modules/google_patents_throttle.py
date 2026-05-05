#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Google Patents への連続アクセスをレート制御する共通モジュール。

ロボット判定回避のため、Google Patents (patents.google.com /
patentimages.storage.googleapis.com) を叩く前に必ず `wait()` を呼ぶ。
プロセス全体で last-access 時刻を共有し、最低 INTERVAL 秒の間隔を強制する。

- スレッドセーフ（threading.Lock で保護）
- 同一プロセス内でのみ有効（ProcessPool で並列実行する場合はそれぞれの
  プロセスでカウントするので、Google Patents へのDLは ProcessPool で
  分散させないこと）
"""
import os
import threading
import time

# ユーザ要件: 1文献あたり2秒以上空ける（ロボット判定回避）
INTERVAL = float(os.environ.get("GOOGLE_PATENTS_INTERVAL", "2.0"))

_last_access = 0.0
_lock = threading.Lock()


def wait():
    """前回アクセスから INTERVAL 秒未満なら不足分だけ sleep。

    呼び出し直前に1回呼ぶ。並列スレッドからでも順次直列化される。
    """
    global _last_access
    with _lock:
        now = time.monotonic()
        elapsed = now - _last_access
        if elapsed < INTERVAL:
            time.sleep(INTERVAL - elapsed)
        _last_access = time.monotonic()


def reset():
    """テスト用: last-access 時刻をリセット"""
    global _last_access
    with _lock:
        _last_access = 0.0
