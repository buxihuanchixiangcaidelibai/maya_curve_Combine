# Maya 非破坏曲线合并工具

`maya_merge_curves.py`（v2.1.0）用于把新曲线控制器的全部 Shape 合并进已经参与层级或约束绑定的老控制器。脚本没有 UI，完全按照选择顺序执行。

## 行为

- 第一选择是老曲线，第二选择是新曲线。
- 支持每个 Transform 下包含一个或多个可见 NURBS Curve Shape。
- 自动寻找两组 Shape 的最近位置，并增加一条独立直线 Shape。
- 新控制器的所有 Shape 会烘焙复制到老 Transform 下。
- 保留老曲线的 Transform、名称、父子层级、子节点和 Transform 约束。
- 老控制器原有 Shape 完全不修改。
- 新曲线不会被删除。
- 合并结果是一次性烘焙结果，不创建构造历史。
- 整个操作是一个 Maya Undo 步骤。

## 使用

脚本代码和启动入口已经放在同一个 `maya_merge_curves.py` 文件里：

1. 先选择已经绑定的老曲线。
2. 按住 `Ctrl` 或 `Shift` 再选择摆放好的新曲线。
3. 在 Maya Script Editor 的 Python 页签加载整个 `maya_merge_curves.py`。
4. 点击 **Execute All**，文件末尾的 `main()` 会立即执行合并。

Maya 2020 也可以直接用一条命令运行这个完整文件（把路径改成脚本实际保存位置）：

```python
execfile(r"D:\maya_tools\maya_merge_curves.py")
```

## 限制

- 老曲线不能来自只读 Reference，也不能是锁定节点。
- 本工具保留的是 Transform 层级与约束；不转移 `skinCluster` 或 CV 权重。
- 连接线是老 Transform 下的独立 Curve Shape；多 Shape 控制器本身不是单条连续 NURBS。
