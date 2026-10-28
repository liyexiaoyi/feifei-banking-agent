"""Bounded offline accumulator for Anthropic Messages SSE streams.

Only public text blocks are kept. Non-text blocks (thinking, tool_use,
signatures, ...) become {"type": "omitted"} placeholders and their payloads
are discarded immediately; nothing hidden is reconstructed or executed.
"""
import json

__all__ = ["MessageStream", "StreamIncomplete", "StreamLimit"]


class StreamIncomplete(ValueError):
    """Stream ended early, left a frame unterminated, or carried an error."""


class StreamLimit(ValueError):
    """Decoded SSE bytes exceeded the fixed parser budget."""


def _reject_constant(_name):
    raise ValueError("non-finite JSON constant")


def _unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON field")
        out[key] = value
    return out


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class MessageStream:
    def __init__(self, max_bytes=220000):
        if not _count(max_bytes) or max_bytes == 0:
            raise ValueError("invalid max_bytes")
        self._max = max_bytes
        self._total = 0
        self._buf = bytearray()
        self._event = None
        self._data = []
        self._msg = None
        self._usage = {}
        self._content = []
        self._open = None
        self._text_open = False
        self._delta_seen = False
        self._stop_reason = None
        self._done = False
        self._failed = None

    def feed(self, chunk):
        if self._failed is not None:
            raise self._failed("stream already failed")
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("chunk must be bytes")
        try:
            self._total += len(chunk)
            if self._total > self._max:
                raise StreamLimit("stream exceeds byte limit")
            self._buf += chunk
            while True:
                nl = self._buf.find(b"\n")
                if nl < 0:
                    break
                line = bytes(self._buf[:nl])
                del self._buf[:nl + 1]
                if line.endswith(b"\r"):
                    line = line[:-1]
                self._line(line)
        except StreamIncomplete:
            self._fail(StreamIncomplete)
            raise
        except ValueError:
            self._fail(ValueError)
            raise

    def finish(self):
        if self._failed is not None:
            raise self._failed("stream already failed")
        if self._buf or self._data or self._event is not None:
            raise StreamIncomplete("unterminated SSE frame")
        if not self._done:
            raise StreamIncomplete("stream ended before message_stop")
        return {**self._msg, "content": [dict(b) for b in self._content],
                "stop_reason": self._stop_reason, "usage": dict(self._usage)}

    def _fail(self, kind):
        self._failed = kind
        self._buf = bytearray()
        self._event, self._data, self._content = None, [], []

    def _line(self, line):
        if b"\r" in line:
            raise ValueError("bare CR line ending unsupported")
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("invalid UTF-8 in stream") from None
        if not text:
            self._dispatch()
            return
        if text.startswith(":"):
            return  # SSE comment
        name, sep, value = text.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        # id/retry/unknown SSE fields are ignored per the SSE spec

    def _dispatch(self):
        event, data = self._event, self._data
        self._event, self._data = None, []
        if not data:
            return
        try:
            obj = json.loads("\n".join(data), parse_constant=_reject_constant,
                             object_pairs_hook=_unique_pairs)
        except (ValueError, RecursionError):
            raise ValueError("invalid frame JSON") from None
        if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
            raise ValueError("invalid frame")
        if event is not None and event != obj["type"]:
            raise ValueError("event name mismatch")
        self._handle(obj["type"], obj)

    def _merge_usage(self, usage):
        if usage is None:
            return
        if not isinstance(usage, dict):
            raise ValueError("invalid usage")
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if value is None:
                continue
            if not _count(value):
                raise ValueError("invalid usage")
            self._usage[key] = value

    def _handle(self, kind, obj):
        if kind == "error":
            raise StreamIncomplete("stream error event")
        if self._done:
            raise ValueError("event after message_stop")
        if kind == "ping":
            return
        if kind == "message_start":
            m = obj.get("message")
            if self._msg is not None:
                raise ValueError("duplicate message_start")
            if (not isinstance(m, dict) or m.get("type") != "message"
                    or m.get("role") != "assistant" or not isinstance(m.get("id"), str)
                    or not isinstance(m.get("model"), str) or m.get("content") != []):
                raise ValueError("invalid message_start")
            self._msg = {"id": m["id"], "type": "message", "role": "assistant", "model": m["model"]}
            self._merge_usage(m.get("usage"))
            return
        if self._msg is None:
            raise ValueError("event before message_start")
        idx = obj.get("index")
        if kind == "content_block_start":
            block = obj.get("content_block")
            if (self._open is not None or self._delta_seen or not _count(idx)
                    or idx != len(self._content) or not isinstance(block, dict)
                    or not isinstance(block.get("type"), str)):
                raise ValueError("invalid content_block_start")
            self._text_open = block["type"] == "text"
            if self._text_open:
                if not isinstance(block.get("text"), str):
                    raise ValueError("invalid text block")
                self._content.append({"type": "text", "text": block["text"]})
            else:
                self._content.append({"type": "omitted"})  # payload discarded
            self._open = idx
        elif kind == "content_block_delta":
            delta = obj.get("delta")
            if (not _count(idx) or idx != self._open or not isinstance(delta, dict)
                    or not isinstance(delta.get("type"), str)):
                raise ValueError("invalid content_block_delta")
            if self._text_open:
                if delta["type"] == "text_delta":
                    if not isinstance(delta.get("text"), str):
                        raise ValueError("invalid text delta")
                    self._content[idx]["text"] += delta["text"]
                elif delta["type"] != "citations_delta":
                    raise ValueError("invalid text delta")
            # non-text deltas are dropped without being stored
        elif kind == "content_block_stop":
            if not _count(idx) or idx != self._open:
                raise ValueError("invalid content_block_stop")
            self._open, self._text_open = None, False
        elif kind == "message_delta":
            delta = obj.get("delta")
            if (self._open is not None or self._stop_reason is not None or not isinstance(delta, dict)
                    or (delta.get("stop_reason") is not None and not isinstance(delta["stop_reason"], str))):
                raise ValueError("invalid message_delta")
            self._stop_reason = delta.get("stop_reason")
            self._merge_usage(obj.get("usage"))
            self._delta_seen = True
        elif kind == "message_stop":
            if self._open is not None or not self._delta_seen or self._stop_reason is None:
                raise ValueError("invalid message_stop")
            self._done = True
        # other event types are ignored for forward compatibility
