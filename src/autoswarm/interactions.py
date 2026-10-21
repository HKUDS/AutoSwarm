"""Response contracts for native questions; no replacement agent conversation."""
from __future__ import annotations

from .records import SwarmError


def fail(message):
    raise SwarmError("invalid_answer", message)


def strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate(request, response):
    """Validate before publication so a bad answer cannot consume a native request."""
    if not isinstance(response, dict):
        fail("Answer must be a JSON object; follow response_format in the request")
    kind = request["response_format"]
    params = request["native_request"]
    if kind == "elicitation":
        if response.get("action") not in {"accept", "decline", "cancel"}:
            fail("Use action accept, decline or cancel")
        if response["action"] == "accept" and params.get("mode", "form") != "url":
            content = response.get("content")
            if not isinstance(content, dict):
                fail("Accepted form content must be a JSON object")
            schema = params.get("requestedSchema", params.get("requested_schema", {}))
            check_schema(content, schema)
        elif response.get("content") is not None:
            fail("Only accepted forms carry content")
        return
    if kind == "codex-async-question":
        if not isinstance(response.get("text"), str) or not response["text"].strip():
            fail("Provide text answering the native asynchronous question")
        return
    if kind == "cursor-plan":
        if response.get("outcome") not in {"accepted", "rejected", "cancelled"}:
            fail("Use outcome accepted, rejected or cancelled")
        return
    if kind == "cursor-question":
        if response.get("outcome") in {"skipped", "cancelled"}:
            return
        if response.get("outcome") != "answered":
            fail("Use outcome answered, skipped or cancelled")
        answers = response.get("answers")
        questions = params.get("questions", [])
        indexed = by_id(answers, "questionId", [q["id"] for q in questions])
        for q in questions:
            labels = indexed[q["id"]].get("selectedOptionIds")
            if not strings(labels) or not labels or len(labels) != len(set(labels)):
                fail("Provide selectedOptionIds for each question")
            if not q.get("allowMultiple") and len(labels) != 1:
                fail("This question permits one option")
            if not set(labels) <= {o["id"] for o in q.get("options", [])}:
                fail("Use native option IDs")
        return
    if response.get("action") == "cancel":
        if kind == "codex-question":
            fail("Codex requires an answers object (empty answers may skip a question)")
        return
    questions = params.get("questions", [])
    if kind in {"claude-question", "codebuddy-question"}:
        questions = params.get("input", params.get("toolCall", {}).get("rawInput", params)).get("questions", [])
        answers = response.get("answers")
        if not isinstance(answers, dict) or set(answers) != {q["question"] for q in questions}:
            fail("answers must contain every exact question text, without extra keys")
        for q in questions:
            value = answers[q["question"]]
            labels = value if isinstance(value, list) else [value]
            if not strings(labels) or not labels or (not q.get("multiSelect") and len(labels) != 1):
                fail("Answer each question with a string, or a string array for multiSelect")
        return
    if kind == "codex-question":
        answers = response.get("answers")
        if not isinstance(answers, dict) or set(answers) != {q["id"] for q in questions}:
            fail("answers must contain every native question ID")
        for q in questions:
            value = answers[q["id"]]
            if not isinstance(value, dict) or not strings(value.get("answers")):
                fail("Each question ID needs {\"answers\":[\"text or label\"]}")
        return
    if kind == "opencode-question":
        answers = response.get("answers")
        if not isinstance(answers, list) or len(answers) != len(questions):
            fail("answers must contain one string array per question in native order")
        for q, labels in zip(questions, answers):
            if not strings(labels) or not labels or (not q.get("multiple") and len(labels) != 1):
                fail("Use one label, or several when multiple=true")
            if q.get("custom") is False and not set(labels) <= {o["label"] for o in q.get("options", [])}:
                fail("This question does not allow custom answers")
        return
    if kind == "muse-question":
        if response.get("action") == "clarify":
            clarification = response.get("clarification")
            if not isinstance(clarification, dict) or clarification.get("format") != "text" or not isinstance(clarification.get("content"), str):
                fail("Use clarification {format:text, content:...}")
            return
        if response.get("action", "answer") != "answer":
            fail("Use action answer, cancel or clarify")
        indexed = by_id(response.get("answers"), "questionId", [q["id"] for q in questions])
        for q in questions:
            answer = indexed[q["id"]]
            fields = set(answer) & {"selectedLabel", "selectedLabels", "freeText"}
            if len(fields) != 1 or set(answer) - {"questionId", "note", *fields}:
                fail("Each answer needs exactly one of selectedLabel, selectedLabels or freeText")
            field = fields.pop()
            value = answer[field]
            if field in {"freeText", "selectedLabel"} and not isinstance(value, str):
                fail("freeText and selectedLabel must be strings")
            if "note" in answer and not isinstance(answer["note"], str):
                fail("note must be a string")
            if field != "freeText":
                selection = q.get("selection", {})
                if (field == "selectedLabels") != (selection.get("mode") == "multiple"):
                    fail("Selection field must match the native question mode")
                labels = value if field == "selectedLabels" else [value]
                if not strings(labels) or len(labels) != len(set(labels)) or not set(labels) <= {o["label"] for o in q.get("options", [])}:
                    fail("Use distinct native option labels")
                if len(labels) < selection.get("minSelections", 0) or ("maxSelections" in selection and len(labels) > selection["maxSelections"]):
                    fail("Selection does not meet the native question's bounds")
        return
    fail("Unsupported response format; ask the assigning parent to inspect this request")


def by_id(values, key, expected):
    if not isinstance(values, list) or any(not isinstance(v, dict) or not isinstance(v.get(key), str) for v in values):
        fail("Provide one answer object per question")
    indexed = {v[key]: v for v in values}
    if len(indexed) != len(values) or set(indexed) != set(expected):
        fail("Answer every question exactly once using its native ID")
    return indexed


def check_schema(value, schema):
    """Validate common MCP form constraints; native validation stays authoritative."""
    if not isinstance(schema, dict):
        fail("Native form schema is not an object; inspect the request")
    types = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
             "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
             "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
             "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "null": lambda v: v is None}
    expected = schema.get("type")
    if isinstance(expected, str) and expected in types and not types[expected](value):
        fail("Form content does not match native type " + expected)
    if "enum" in schema and value not in schema["enum"]:
        fail("Form value is outside the native enum")
    if isinstance(value, dict):
        if not set(schema.get("required", [])) <= set(value):
            fail("Form is missing required fields")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            fail("Form contains unknown fields")
        for key, item in value.items():
            if key in props:
                check_schema(item, props[key])
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for item in value:
            check_schema(item, schema["items"])


def contract(kind):
    return {
        "codex-async-question": '{"text":"answer or clarification to these questions"}; delivered as native input to the same active turn',
        "codex-question": '{"answers":{"question-id":{"answers":["label or text"]}}}',
        "claude-question": '{"answers":{"exact question text":"label or text"}} (multiSelect accepts arrays); or {"action":"cancel"}',
        "codebuddy-question": '{"answers":{"exact question text":"label or text"}} (multiSelect accepts arrays); or {"action":"cancel"}',
        "cursor-question": '{"outcome":"answered","answers":[{"questionId":"id","selectedOptionIds":["option-id"]}]}; or {"outcome":"skipped"|"cancelled"}',
        "cursor-plan": '{"outcome":"accepted"|"rejected"|"cancelled","reason":"optional"}',
        "opencode-question": '{"answers":[["label or text"], ["next question answer"]]}; or {"action":"cancel"}',
        "muse-question": '{"action":"answer","answers":[{"questionId":"id","selectedLabel":"label"}]}; use selectedLabels for multiple, freeText for text; action cancel or clarify also supported',
        "elicitation": '{"action":"accept","content":{...native requestedSchema...}}; or {"action":"decline"|"cancel"}. URL accept carries no content and requires actual user consent.',
    }[kind]
