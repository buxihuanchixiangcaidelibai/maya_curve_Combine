# -*- coding: utf-8 -*-
from __future__ import division, print_function

r"""Non-destructive multi-shape controller merge for Autodesk Maya 2020+.

The first selected curve controller is the bound/old controller.  Its
transform, hierarchy, children, constraints, and existing curve shapes are
preserved.  Every visible curve shape from the second controller is baked into
the old transform, and an independent straight bridge shape is inserted at the
closest points.  The second controller is retained as a source object.

Run in Maya's Script Editor (Python).  Select the old controller first and
the new controller second, load this entire file, and choose Execute All.
The launch call is included at the bottom of this file.
"""

import math
import re

import maya.api.OpenMaya as om
import maya.cmds as cmds


TOOL_VERSION = "2.1.0"


def _short_name(node):
    return node.rsplit("|", 1)[-1]


def _safe_name(node):
    return re.sub(r"[^A-Za-z0-9_]", "_", _short_name(node).replace(":", "_"))


def _long_name(node):
    matches = cmds.ls(node, long=True) or []
    if not matches:
        raise RuntimeError(u"找不到节点：{0}".format(node))
    return matches[0]


def _as_transform(node):
    """Resolve a transform, curve shape, or curve component to its transform."""
    node = node.split(".", 1)[0]
    node = _long_name(node)
    node_type = cmds.nodeType(node)
    if node_type in ("transform", "joint"):
        return node
    if node_type == "nurbsCurve":
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if parents:
            return parents[0]
    raise RuntimeError(u"所选对象不是 NURBS 曲线：{0}".format(node))


def _visible_curve_shapes(transform):
    return cmds.listRelatives(
        transform,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
        type="nurbsCurve",
    ) or []


def _dag_path(node):
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _curve_fn(shape):
    return om.MFnNurbsCurve(_dag_path(shape))


def _distance_squared(first, second):
    delta = first - second
    return delta.x * delta.x + delta.y * delta.y + delta.z * delta.z


def _all_curve_parameters(curve_fn):
    """Return parameters for a closest-point search anywhere on a curve."""
    start, end = curve_fn.knotDomain
    count = max(48, min(512, int(curve_fn.numSpans) * 16))
    step = (end - start) / float(count)
    parameters = [start + step * index for index in range(count)]
    if curve_fn.form == om.MFnNurbsCurve.kOpen:
        parameters.append(end)
    return parameters


def _closest_points_anywhere(first_shape, second_shape):
    """Return the closest world-space pair on two curve shapes."""
    first_fn = _curve_fn(first_shape)
    second_fn = _curve_fn(second_shape)
    best = None

    for parameter in _all_curve_parameters(first_fn):
        first_point = first_fn.getPointAtParam(parameter, om.MSpace.kWorld)
        second_point, second_parameter = second_fn.closestPoint(
            first_point, tolerance=1.0e-7, space=om.MSpace.kWorld
        )
        candidate = (
            _distance_squared(first_point, second_point),
            first_shape,
            second_shape,
            float(parameter),
            float(second_parameter),
            first_point,
            second_point,
        )
        if best is None or candidate[0] < best[0]:
            best = candidate

    for parameter in _all_curve_parameters(second_fn):
        second_point = second_fn.getPointAtParam(parameter, om.MSpace.kWorld)
        first_point, first_parameter = first_fn.closestPoint(
            second_point, tolerance=1.0e-7, space=om.MSpace.kWorld
        )
        candidate = (
            _distance_squared(first_point, second_point),
            first_shape,
            second_shape,
            float(first_parameter),
            float(parameter),
            first_point,
            second_point,
        )
        if best is None or candidate[0] < best[0]:
            best = candidate
    return best


def closest_shape_pair(first_shapes, second_shapes):
    """Find the closest pair across two multi-shape controllers."""
    best = None
    for first_shape in first_shapes:
        for second_shape in second_shapes:
            candidate = _closest_points_anywhere(first_shape, second_shape)
            if best is None or candidate[0] < best[0]:
                best = candidate
    if best is None:
        raise RuntimeError(u"无法计算两组曲线之间的最近点。")
    return {
        "distance": math.sqrt(best[0]),
        "old_shape": best[1],
        "new_shape": best[2],
        "old_parameter": best[3],
        "new_parameter": best[4],
        "old_point": tuple(float(best[5][axis]) for axis in range(3)),
        "new_point": tuple(float(best[6][axis]) for axis in range(3)),
    }


def _curve_shape(transform):
    shapes = cmds.listRelatives(
        transform,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
        type="nurbsCurve",
    ) or []
    if not shapes:
        raise RuntimeError(u"临时曲线没有有效 Shape：{0}".format(transform))
    return shapes[0]


def _straight_bridge(first_point, second_point, degree, name):
    degree = max(1, min(7, int(degree)))
    points = []
    for index in range(degree + 1):
        factor = index / float(degree)
        points.append(
            tuple(
                first_point[axis]
                + (second_point[axis] - first_point[axis]) * factor
                for axis in range(3)
            )
        )
    return cmds.curve(name=name, degree=degree, point=points)


def _copy_display_attributes(source_shape, destination_shape):
    attributes = (
        "overrideEnabled",
        "overrideDisplayType",
        "overrideLevelOfDetail",
        "overrideShading",
        "overrideTexturing",
        "overridePlayback",
        "overrideRGBColors",
        "overrideColor",
        "overrideColorR",
        "overrideColorG",
        "overrideColorB",
        "lineWidth",
        "alwaysDrawOnTop",
    )
    for attribute in attributes:
        source_plug = source_shape + "." + attribute
        destination_plug = destination_shape + "." + attribute
        if not cmds.objExists(source_plug) or not cmds.objExists(destination_plug):
            continue
        try:
            value = cmds.getAttr(source_plug)
            if isinstance(value, (list, tuple)) and len(value) == 1:
                value = value[0]
            if isinstance(value, (list, tuple)):
                cmds.setAttr(destination_plug, *value)
            else:
                cmds.setAttr(destination_plug, value)
        except (RuntimeError, TypeError, ValueError):
            pass


def _move_result_shape_under_old_transform(result_transform, old_transform):
    """Bake world geometry into old-transform space, then move its shape."""
    cmds.parent(result_transform, old_transform, absolute=True)
    cmds.makeIdentity(
        result_transform,
        apply=True,
        translate=True,
        rotate=True,
        scale=True,
        normal=False,
    )
    result_shape = _curve_shape(result_transform)
    parented = cmds.parent(
        result_shape,
        old_transform,
        shape=True,
        relative=True,
    ) or []
    if not parented:
        raise RuntimeError(u"无法把合并结果放入老曲线 Transform。")
    new_shape = parented[0]
    if cmds.objExists(result_transform):
        cmds.delete(result_transform)
    return _long_name(new_shape)


def _duplicate_curve_shape_in_world(source_shape, name):
    """Bake one source shape into an independent transform in world space."""
    source_parent = cmds.listRelatives(
        source_shape, parent=True, fullPath=True
    ) or []
    if not source_parent:
        raise RuntimeError(u"曲线 Shape 没有 Transform：{0}".format(source_shape))
    source_matrix = cmds.xform(
        source_parent[0], query=True, worldSpace=True, matrix=True
    )
    duplicate = cmds.duplicateCurve(
        source_shape,
        constructionHistory=False,
        local=True,
        name=name,
    )[0]
    # Applying the inclusive matrix also handles shapes below nested offset
    # groups; duplicateCurve alone only copies the immediate local transform.
    cmds.xform(duplicate, worldSpace=True, matrix=source_matrix)
    return duplicate


def _validate_inputs(old_transform, new_transform):
    if old_transform == new_transform:
        raise RuntimeError(u"老曲线和新曲线不能是同一个对象。")
    if cmds.referenceQuery(old_transform, isNodeReferenced=True):
        raise RuntimeError(u"老曲线来自 Reference，无法安全替换它的 Shape。")
    if (cmds.lockNode(old_transform, query=True, lock=True) or [False])[0]:
        raise RuntimeError(u"老曲线 Transform 已锁定，请先解锁节点。")


def _selection_pair(selection=None):
    # orderedSelection already means "read the active selection".  Combining
    # it with selection=True causes some Maya versions to emit:
    # "Only one of -sl/-hl/-lv flags may be specified".
    selection = selection or cmds.ls(
        orderedSelection=True, long=True, flatten=True
    ) or []
    transforms = []
    for node in selection:
        try:
            transform = _as_transform(node)
        except RuntimeError:
            continue
        if transform not in transforms:
            transforms.append(transform)
    if len(transforms) != 2:
        raise RuntimeError(u"请按顺序选择两条曲线：先选老曲线，再选新曲线。")
    return transforms[0], transforms[1]


def merge_curves(old_curve, new_curve):
    """Bake all shapes of *new_curve* under *old_curve* plus a bridge.

    Returns a dictionary describing the result.  The whole operation is one
    Maya undo step.  If anything fails, the partial operation is rolled back.
    """
    old_transform = _as_transform(old_curve)
    new_transform = _as_transform(new_curve)
    old_shapes = _visible_curve_shapes(old_transform)
    new_shapes = _visible_curve_shapes(new_transform)
    if not old_shapes:
        raise RuntimeError(u"老曲线没有可见的 NURBS Curve Shape。")
    if not new_shapes:
        raise RuntimeError(u"新曲线没有可见的 NURBS Curve Shape。")
    _validate_inputs(old_transform, new_transform)
    if not cmds.undoInfo(query=True, state=True):
        raise RuntimeError(u"Maya Undo 当前已关闭；请开启 Undo 后再执行合并。")

    old_uuid = (cmds.ls(old_transform, uuid=True) or [None])[0]
    old_parent = cmds.listRelatives(old_transform, parent=True, fullPath=True) or []
    prefix = _safe_name(old_transform)
    closest = closest_shape_pair(old_shapes, new_shapes)
    undo_open = False
    edit_started = False
    failure = None
    result_data = None

    cmds.undoInfo(openChunk=True, chunkName="NonDestructiveMergeCurves")
    undo_open = True
    try:
        # The marker guarantees that a failed operation owns the next undo
        # item.  Without it, an early Maya command failure could accidentally
        # undo the user's previous action instead of this operation.
        undo_marker = cmds.createNode(
            "network", name="__{0}_mergeUndoMarker#".format(prefix)
        )
        edit_started = True

        added_shapes = []
        for index, source_shape in enumerate(new_shapes, 1):
            duplicate = _duplicate_curve_shape_in_world(
                source_shape,
                "__{0}_part{1}#".format(prefix, index),
            )
            added_shape = _move_result_shape_under_old_transform(
                duplicate, old_transform
            )
            _copy_display_attributes(old_shapes[0], added_shape)
            added_shape = cmds.rename(
                added_shape,
                _short_name(old_transform) + "MergedPartShape",
            )
            added_shapes.append(_long_name(added_shape))

        bridge = _straight_bridge(
            closest["old_point"],
            closest["new_point"],
            1,
            "__{0}_bridge#".format(prefix),
        )
        bridge_shape = _move_result_shape_under_old_transform(
            bridge, old_transform
        )
        _copy_display_attributes(old_shapes[0], bridge_shape)
        bridge_shape = cmds.rename(
            bridge_shape,
            _short_name(old_transform) + "BridgeShape",
        )
        bridge_shape = _long_name(bridge_shape)
        if cmds.objExists(undo_marker):
            cmds.delete(undo_marker)

        # Verify the operation did not replace or reparent the bound transform.
        current_uuid = (cmds.ls(old_transform, uuid=True) or [None])[0]
        current_parent = (
            cmds.listRelatives(old_transform, parent=True, fullPath=True) or []
        )
        if old_uuid != current_uuid or old_parent != current_parent:
            raise RuntimeError(u"安全检查失败：老曲线 Transform 或父级发生变化。")

        cmds.select(old_transform, replace=True)
        result_data = {
            "transform": old_transform,
            "added_shapes": added_shapes,
            "bridge_shape": bridge_shape,
            "old_shapes": old_shapes,
            "new_source": new_transform,
            "bridge_length": float(closest["distance"]),
        }
    except Exception as error:
        failure = error
    finally:
        if undo_open:
            cmds.undoInfo(closeChunk=True)

    if failure is not None:
        try:
            if edit_started:
                cmds.undo()
        except RuntimeError:
            pass
        raise failure
    return result_data


def merge_selected_curves(selection=None):
    old_transform, new_transform = _selection_pair(selection)
    result = merge_curves(old_transform, new_transform)
    message = u"合并完成：{0}（连接长度 {1:.3f}）".format(
        _short_name(result["transform"]), result["bridge_length"]
    )
    if not cmds.about(batch=True):
        cmds.inViewMessage(
            assistMessage=message,
            position="midCenterTop",
            fade=True,
        )
    print(
        "Merge complete: {0} (bridge length {1:.3f})".format(
            _short_name(result["transform"]), result["bridge_length"]
        )
    )
    return result


def main():
    """Entry point used by Execute All in Maya's Python Script Editor."""
    return merge_selected_curves()


# The implementation and launch command intentionally live in this one file.
# Importing it as a module is safe; executing the whole file runs immediately.
if __name__ == "__main__":
    main()
