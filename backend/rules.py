"""桥梁微应变判定：按荷载等级闭区间（含上下限）判定合格/越界。"""


def judge_microstrain(
    microstrain: float,
    lower_bound: float = 80.0,
    upper_bound: float = 220.0,
    grade_label: str = "",
) -> tuple[str, str]:
    """按给定荷载等级的闭区间 [lower_bound, upper_bound] 判定。

    上下限由调用方在“领取读数”的瞬间从分档表抄录后传入，
    处理中的单据不再受后续改档影响。
    """
    band = f"{lower_bound:g}～{upper_bound:g}"
    prefix = f"{grade_label}：" if grade_label else ""
    if lower_bound <= microstrain <= upper_bound:
        return (
            "合格",
            f"{prefix}微应变 {microstrain:g} 处于闭区间 {band} με 允许范围内",
        )
    if microstrain < lower_bound:
        return (
            "越界",
            f"{prefix}微应变 {microstrain:g} 低于闭区间下限 {lower_bound:g} με",
        )
    return (
        "越界",
        f"{prefix}微应变 {microstrain:g} 高于闭区间上限 {upper_bound:g} με",
    )
