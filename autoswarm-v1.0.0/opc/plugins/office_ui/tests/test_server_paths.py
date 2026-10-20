from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from opc.plugins.office_ui.server import _is_under_path


def test_is_under_path_accepts_platform_child_paths() -> None:
    cases = [
        (
            PureWindowsPath(r"C:\work\OpenOPC\opc\plugins\office_ui\frontend_dist\assets\index.js"),
            PureWindowsPath(r"C:\work\OpenOPC\opc\plugins\office_ui\frontend_dist\assets"),
        ),
        (
            PurePosixPath("/work/OpenOPC/opc/plugins/office_ui/frontend_dist/assets/index.js"),
            PurePosixPath("/work/OpenOPC/opc/plugins/office_ui/frontend_dist/assets"),
        ),
    ]

    for child, base in cases:
        assert _is_under_path(child, base)


def test_is_under_path_rejects_sibling_prefixes() -> None:
    cases = [
        (
            PureWindowsPath(r"C:\work\OpenOPC\opc\plugins\office_ui\frontend_dist\assets-old\index.js"),
            PureWindowsPath(r"C:\work\OpenOPC\opc\plugins\office_ui\frontend_dist\assets"),
        ),
        (
            PurePosixPath("/work/OpenOPC/opc/plugins/office_ui/frontend_dist/assets-old/index.js"),
            PurePosixPath("/work/OpenOPC/opc/plugins/office_ui/frontend_dist/assets"),
        ),
    ]

    for child, base in cases:
        assert not _is_under_path(child, base)


def test_is_under_path_rejects_traversal_after_resolution() -> None:
    cases = [
        (
            PureWindowsPath(r"C:\work\OpenOPC\opc\plugins\office_ui\frontend_dist\index.html"),
            PureWindowsPath(r"C:\work\OpenOPC\opc\plugins\office_ui\frontend_dist\assets"),
        ),
        (
            PurePosixPath("/work/OpenOPC/opc/plugins/office_ui/frontend_dist/index.html"),
            PurePosixPath("/work/OpenOPC/opc/plugins/office_ui/frontend_dist/assets"),
        ),
    ]

    for escaped, base in cases:
        assert not _is_under_path(escaped, base)
