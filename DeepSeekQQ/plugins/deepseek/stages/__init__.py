"""Pipeline 阶段模块包。

各 stage 文件通过 @stage() 装饰器在 import 时自动注册到 pipeline._PIPELINE。
import 顺序决定了 Pipeline 执行顺序，由 handler.py 中的内联 import 控制。
"""