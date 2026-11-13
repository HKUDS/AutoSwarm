"""Print the Windows environment variables a new login would get, as JSON (Desk merges them).

{"machine": [[name, type, value]], "user": [...]}, type "REG_SZ" or "REG_EXPAND_SZ"
(unexpanded). Elsewhere both lists are empty.
"""
import json
import sys


def rows(root, key):
    import winreg
    found = []
    try:
        with winreg.OpenKey(root, key) as handle:
            for index in range(winreg.QueryInfoKey(handle)[1]):
                name, value, kind = winreg.EnumValue(handle, index)
                if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
                    found.append([name, "REG_EXPAND_SZ" if kind == winreg.REG_EXPAND_SZ else "REG_SZ", value])
    except OSError:
        pass
    return found


def main():
    value = {"machine": [], "user": []}
    if sys.platform == "win32":
        import winreg
        value = {"machine": rows(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                 "user": rows(winreg.HKEY_CURRENT_USER, "Environment")}
    sys.stdout.write(json.dumps(value))


if __name__ == "__main__":
    main()
