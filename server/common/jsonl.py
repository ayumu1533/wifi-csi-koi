"""JSONL（1行1レコードのJSON）ファイルの読み書き。収集データ（calib.jsonl など）の形式。"""
from __future__ import annotations

import json


def load_jsonl(path: str) -> list[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def append_jsonl(path: str, records: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
