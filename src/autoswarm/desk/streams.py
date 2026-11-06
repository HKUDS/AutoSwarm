"""Bounded backward paging of complete JSONL lines, including raw fallbacks."""
import json
from .lens import normalize

PAGE_BYTES = 512 * 1024
PAGE_LINES = 400


def page(directory, provider, session=None, before=None, name="events.jsonl"):
    """`name` is events.jsonl (the kept copy) or live.jsonl (a running call's
    temporary stream). Lines before an AutoSwarm "prompt" mark are a replayed
    transcript (ACP session/load) and belong to earlier turns."""
    path = directory / name
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            end = min(size, before) if before is not None else size
            start = max(0, end - PAGE_BYTES)
            stream.seek(start)
            chunk = stream.read(end - start)
        if start:
            first = chunk.find(b"\n")
            if first < 0:
                return {"events": [], "before": start, "size": size, "available": True, "warning": "单条事件超过读取窗口，可在记录文件中查看。"}
            start += first + 1
            chunk = chunk[first + 1:]
        # An unfinished UTF-8 / JSON line must never be consumed.
        chunk = chunk[:chunk.rfind(b"\n") + 1]
        lines = chunk.splitlines(keepends=True)
        if len(lines) > PAGE_LINES:
            start += sum(map(len, lines[:-PAGE_LINES]))
            lines = lines[-PAGE_LINES:]
        cursor, events, state, replayed = start, [], {}, False
        for line in lines:
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                value = line.decode("utf-8", errors="replace").rstrip()
            if isinstance(value, dict) and value.get("autoswarm") == "prompt":
                events.clear()
                replayed = True
            events.extend({**item, "seq": f"{cursor}:{index}"} for index, item in enumerate(normalize(provider, value, session, state)))
            cursor += len(line)
        return {"events": events, "before": 0 if replayed else start, "end": cursor, "size": size, "available": True}
    except FileNotFoundError:
        return {"events": [], "before": 0, "size": 0, "available": False}
    except OSError as error:
        return {"events": [], "before": 0, "size": 0, "available": False, "warning": str(error)}
