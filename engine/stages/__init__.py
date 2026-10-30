# -*- coding: utf-8 -*-
"""stage 包。每个 stage 暴露 run(job, cfg) -> dict。"""

STAGES = ["fetch", "separate", "select", "clean", "dataset", "train", "convert", "mix", "qa", "export"]

__all__ = ["STAGES"]
