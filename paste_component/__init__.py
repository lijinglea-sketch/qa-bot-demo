import os
import streamlit.components.v1 as components

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_component_func = components.declare_component("paste_image_input", path=_THIS_DIR)


def paste_image_input(key=None):
    """
    粘贴/拖拽图片组件。
    返回 data URI 字符串列表，或 None（无图片时）。
    """
    return _component_func(key=key, default=None)
