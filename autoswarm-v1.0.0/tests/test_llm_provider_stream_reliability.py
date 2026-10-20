import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from opc.core.config import LLMConfig
from opc.llm.provider import LLMProvider


def completion():
    return NS(usage=None, choices=[NS(finish_reason="stop", message=NS(content="done", tool_calls=[]))])


class ProviderStreamReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, **kwargs):
        return LLMProvider(LLMConfig(default_model="openai/test-proxy-model", max_tokens=64000, **kwargs))

    async def test_output_limit_retry_is_endpoint_and_model_scoped(self):
        provider = self.provider()
        requests = []
        async def call(**kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                raise ValueError("`max_tokens` invalid: expected a value <= 32768, but got 64000 instead")
            return completion()
        with patch("opc.llm.provider.litellm.acompletion", side_effect=call):
            await provider.chat([{"role": "user", "content": "a"}], reasoning_effort="high")
            await provider.chat([{"role": "user", "content": "b"}])
            provider._api_base = "https://different-provider.invalid/v1"
            await provider.chat([{"role": "user", "content": "c"}])
        self.assertEqual([item["max_tokens"] for item in requests], [64000, 32768, 32768, 64000])
        self.assertEqual(requests[0]["messages"], requests[1]["messages"])
        self.assertEqual(requests[1]["reasoning_effort"], "high")
        self.assertEqual(provider.config.max_tokens, 64000)

    async def test_cap_retry_is_bounded_and_quota_is_not_retried(self):
        for error, calls in (("max_tokens expected a value <= 100", 2), ("429 quota exceeded", 1)):
            mock = AsyncMock(side_effect=ValueError(error))
            with patch("opc.llm.provider.litellm.acompletion", mock):
                with self.assertRaises(ValueError):
                    await self.provider().chat([])
            self.assertEqual(mock.await_count, calls)

    async def test_stream_usage_option_falls_back_only_when_auto_added(self):
        calls = []
        async def call(**kwargs):
            calls.append(kwargs)
            if "stream_options" in kwargs:
                raise ValueError("Unsupported parameter: stream_options")
            return completion()
        with patch("opc.llm.provider.litellm.acompletion", side_effect=call):
            events = [event async for event in self.provider().chat_stream([])]
        self.assertEqual(calls[0]["stream_options"], {"include_usage": True})
        self.assertNotIn("stream_options", calls[1])
        self.assertEqual([event.event_type for event in events], ["message_start", "assistant_delta", "message_stop"])
        mock = AsyncMock(side_effect=ValueError("Unsupported parameter: stream_options"))
        with patch("opc.llm.provider.litellm.acompletion", mock):
            with self.assertRaises(ValueError):
                _ = [event async for event in self.provider().chat_stream([], stream_options={"include_usage": False})]
        self.assertEqual(mock.await_count, 1)

    async def test_cumulative_usage_is_counted_once_and_details_preserved(self):
        async def chunks():
            for count in (100, 100, 80, 120):
                yield NS(choices=[], usage=NS(prompt_tokens=count, completion_tokens=10,
                    prompt_tokens_details=NS(cached_tokens=60), completion_tokens_details=NS(reasoning_tokens=5)))
        provider = self.provider()
        with patch("opc.llm.provider.litellm.acompletion", AsyncMock(return_value=chunks())):
            events = [event async for event in provider.chat_stream([])]
        usage = [event.payload for event in events if event.event_type == "usage"]
        self.assertEqual(sum(item["prompt_tokens"] for item in usage), 120)
        self.assertEqual(sum(item["completion_tokens"] for item in usage), 10)
        self.assertEqual(provider.stats["tokens_in"], 120)
        self.assertEqual(usage[-1]["cached_input_tokens"], 60)
        self.assertEqual(usage[-1]["reasoning_tokens"], 5)
        self.assertEqual(usage[-1]["usage_raw"]["prompt_tokens_details"], {"cached_tokens": 60})

    async def test_stream_is_never_replayed_after_partial_content(self):
        async def chunks():
            yield NS(choices=[NS(delta=NS(content="partial"), finish_reason=None)], usage=None)
            raise ValueError("max_tokens expected a value <= 100")
        mock = AsyncMock(return_value=chunks())
        seen = []
        with patch("opc.llm.provider.litellm.acompletion", mock):
            with self.assertRaises(ValueError):
                async for event in self.provider().chat_stream([]):
                    seen.append(event)
        self.assertEqual(mock.await_count, 1)
        self.assertTrue(any(item.event_type == "assistant_delta" for item in seen))

    async def test_nonstream_fallback_preserves_multiple_tool_ids(self):
        response = completion()
        response.choices[0].message.tool_calls = [NS(id=f"call-{i}", function=NS(name="file_read", arguments="{}")) for i in range(2)]
        with patch("opc.llm.provider.litellm.acompletion", AsyncMock(return_value=response)):
            events = [event async for event in self.provider().chat_stream([])]
        calls = [event.payload for event in events if event.event_type == "tool_call_delta"]
        self.assertEqual([item["index"] for item in calls], [0, 1])
        self.assertEqual([item["id"] for item in calls], ["call-0", "call-1"])

    async def test_dictionary_usage_is_counted_for_chat_and_stream_fallback(self):
        response = completion()
        response.usage = {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 50}}
        provider = self.provider()
        with patch("opc.llm.provider.litellm.acompletion", AsyncMock(return_value=response)):
            result = await provider.chat([])
            events = [event async for event in provider.chat_stream([])]
        self.assertEqual(result["usage"]["prompt_tokens"], 100)
        self.assertEqual(provider.stats["tokens_in"], 200)
        self.assertEqual(provider.stats["tokens_out"], 20)
        self.assertEqual(next(event.payload for event in events if event.event_type == "usage")["cached_input_tokens"], 50)
