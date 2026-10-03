"""桥梁微应变判定：按荷载等级的闭区间 [下限, 上限] 判定合格/越界。

判定带由测量员在「荷载分档」专页维护；工人领取读数的瞬间会把当时
该等级的现行上下限抄到读数上，判定一律吃抄档，故本模块只接收
显式传入的 lower/upper，不读任何全局默认值。
"""

DEFAULT_GRADES = [
    # key, 名称, 下限, 上限
    ("light", "轻载", 80.0, 220.0),
    ("heavy", "重载", 60.0, 260.0),
]


def _fmt(value: float) -> str:
    return f"{value:g}"


def judge_microstrain(
    microstrain: float, lower: float, upper: float, grade_name: str = ""
) -> tuple[str, str]:
    prefix = f"{grade_name} " if grade_name else ""
    band = f"{prefix}{_fmt(lower)}～{_fmt(upper)} με"
    if lower <= microstrain <= upper:
        return "合格", f"微应变处于 {band} 设计允许闭区间内"
    if microstrain < lower:
        return "越界", f"微应变低于 {band} 设计下限"
    return "越界", f"微应变高于 {band} 设计上限"
