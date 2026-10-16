# 파일명: tier_statistics.py
# 런타임 통계: AWS 기준 임계값을 동일하게 사용한다.
from __future__ import annotations
import math
import statistics

def t_critical_975(df):
    table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.16, 14: 2.145, 15: 2.131, 16: 2.12, 17: 2.11, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.08, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.06, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}
    return table.get(max(1, int(df)), 1.96)

def confidence_interval_95(values: list[float]) -> dict[str, float | int | None]:
    """Calculate the arithmetic-mean Student-t 95% interval."""
    sample_count = len(values)
    if sample_count < 2:
        return {'sample_count': sample_count, 'mean': sum(values) / sample_count if values else None, 'lower': None, 'upper': None}
    mean = statistics.mean(values)
    standard_error = statistics.stdev(values) / math.sqrt(sample_count)
    margin = t_critical_975(sample_count - 1) * standard_error
    return {'sample_count': sample_count, 'mean': mean, 'lower': mean - margin, 'upper': mean + margin}
