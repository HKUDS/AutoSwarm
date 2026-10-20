from __future__ import annotations

import importlib
import shutil
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from opc.core.attachment_store import AttachmentRef
from opc.core.config import LLMConfig
from opc.plugins.office_ui.dispatcher import DispatchResult, Dispatcher, Intent
from opc.plugins.office_ui.ws_handler import WSHandler


def _load_provider_module():
    fake_litellm = types.ModuleType("litellm")
    fake_litellm.suppress_debug_info = False
    fake_litellm.drop_params = False
    fake_litellm.get_max_tokens = lambda *_args, **_kwargs: None
    fake_litellm.token_counter = lambda *_args, **_kwargs: 0
    fake_litellm.completion_cost = lambda **_kwargs: 0.0

    async def _unused_completion(**_kwargs):
        raise AssertionError("acompletion should not be called in attachment capability tests")

    fake_litellm.acompletion = _unused_completion
    fake_litellm.exceptions = types.SimpleNamespace(ContextWindowExceededError=RuntimeError)

    with patch.dict(sys.modules, {"litellm": fake_litellm}):
        sys.modules.pop("opc.llm.provider", None)
        provider_module = importlib.import_module("opc.llm.provider")
        return importlib.reload(provider_module)


class TestDispatcherAttachmentRouting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = MagicMock()
        self.engine.store = MagicMock()
        self.engine.memory = MagicMock()
        self.engine.llm = None
        self.dispatcher = Dispatcher(self.engine, MagicMock())

    async def test_handle_routes_messages_with_attachments_to_engine(self) -> None:
        self.dispatcher._classify_intent = AsyncMock(return_value=Intent.CONVERSATION)

        result = await self.dispatcher.handle(
            "task-1",
            "Sent with attachments",
            has_attachments=True,
        )

        self.assertEqual(result.route, "engine")
        self.assertEqual(result.intent, Intent.TASK_REQUEST)
        self.dispatcher._classify_intent.assert_not_awaited()

    async def test_classify_intent_uses_fast_path_for_chinese_task_request(self) -> None:
        self.engine.llm = MagicMock()
        self.engine.llm.simple_chat = AsyncMock(return_value='{"intent":"CONVERSATION"}')

        intent = await self.dispatcher._classify_intent(
            "帮我修复这个 bug",
            "task-1",
            None,
        )

        self.assertEqual(intent, Intent.TASK_REQUEST)
        self.engine.llm.simple_chat.assert_not_awaited()

    async def test_classify_intent_uses_fast_path_for_chinese_status_query(self) -> None:
        self.engine.llm = MagicMock()
        self.engine.llm.simple_chat = AsyncMock(return_value='{"intent":"TASK_REQUEST"}')

        intent = await self.dispatcher._classify_intent(
            "现在进度怎么样了？",
            "task-1",
            None,
        )

        self.assertEqual(intent, Intent.STATUS_QUERY)
        self.engine.llm.simple_chat.assert_not_awaited()

    async def test_classify_intent_uses_fast_path_for_chinese_greeting(self) -> None:
        self.engine.llm = MagicMock()
        self.engine.llm.simple_chat = AsyncMock(return_value='{"intent":"TASK_REQUEST"}')

        intent = await self.dispatcher._classify_intent(
            "你好",
            "task-1",
            None,
        )

        self.assertEqual(intent, Intent.CONVERSATION)
        self.engine.llm.simple_chat.assert_not_awaited()


class TestWSHandlerAttachmentDispatch(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = MagicMock()
        self.engine.project_id = "test-project"
        self.engine.store = None
        self.engine.memory = None
        self.engine.escalation = None
        self.handler = WSHandler(self.engine, MagicMock(), MagicMock(), MagicMock())
        self.handler.broadcast = AsyncMock()
        self.handler._process_session_message = AsyncMock()
        self.handler.dispatcher.handle = AsyncMock(
            return_value=DispatchResult(route="engine", intent=Intent.TASK_REQUEST)
        )

    async def test_dispatch_session_message_marks_attachment_turns_for_engine(self) -> None:
        attachment_refs = [{
            "attachment_id": "att-image",
            "filename": "image.png",
            "mime_type": "image/png",
            "size_bytes": 4,
            "disk_path": "projects/test-project/attachments/att-image/image.png",
        }]

        await self.handler._dispatch_session_message(
            "task-1",
            "Please inspect the image",
            session_id="session-1",
            attachment_refs=attachment_refs,
        )

        self.handler.dispatcher.handle.assert_awaited_once_with(
            "task-1",
            "Please inspect the image",
            session_id="session-1",
            has_attachments=True,
        )
        self.handler._process_session_message.assert_awaited_once_with(
            "task-1",
            "Please inspect the image",
            session_id="session-1",
            attachment_refs=attachment_refs,
        )


class TestLLMProviderAttachmentSupport(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provider_module = _load_provider_module()

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("opc.llm.provider", None)

    def setUp(self) -> None:
        self.test_root = Path.cwd() / ".test_llm_provider_attachments"
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.ref = AttachmentRef(
            attachment_id="att-image",
            filename="image.png",
            mime_type="image/png",
            size_bytes=4,
            disk_path="projects/test-project/attachments/att-image/image.png",
        )
        image_path = self.test_root / self.ref.disk_path
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"\x89PNG")
        self.video_ref = AttachmentRef(
            attachment_id="att-video",
            filename="clip.mp4",
            mime_type="video/mp4",
            size_bytes=4,
            disk_path="projects/test-project/attachments/att-video/clip.mp4",
        )
        video_path = self.test_root / self.video_ref.disk_path
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"\x00\x00\x00\x18ftyp")

    def tearDown(self) -> None:
        shutil.rmtree(self.test_root, ignore_errors=True)

    def test_prepare_user_message_content_supports_providerless_multimodal_models(self) -> None:
        provider = self.provider_module.LLMProvider(
            LLMConfig(default_model="gpt-5.4"),
            opc_home=self.test_root,
        )

        content = provider.prepare_user_message_content(
            "Describe the image",
            attachment_refs=[self.ref.to_dict()],
        )

        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_prepare_user_message_content_supports_azure_prefixed_models(self) -> None:
        provider = self.provider_module.LLMProvider(
            LLMConfig(default_model="azure/gpt-4.1-mini"),
            opc_home=self.test_root,
        )

        content = provider.prepare_user_message_content(
            "Describe the image",
            attachment_refs=[self.ref.to_dict()],
        )

        self.assertIsInstance(content, list)
        self.assertEqual(content[1]["type"], "image_url")

    def test_prepare_user_message_content_supports_poe_video_file_inputs(self) -> None:
        provider = self.provider_module.LLMProvider(
            LLMConfig(
                default_model="openai/gpt-5.4",
                api_base="https://api.poe.com/v1",
            ),
            opc_home=self.test_root,
        )

        content = provider.prepare_user_message_content(
            "Describe the video",
            attachment_refs=[self.video_ref.to_dict()],
        )

        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "file")
        self.assertEqual(content[1]["file"]["filename"], "clip.mp4")
        self.assertTrue(content[1]["file"]["file_data"].startswith("data:video/mp4;base64,"))

    def test_prepare_user_message_content_supports_openrouter_video_urls(self) -> None:
        provider = self.provider_module.LLMProvider(
            LLMConfig(
                default_model="google/gemini-2.5-flash",
                api_base="https://openrouter.ai/api/v1",
            ),
            opc_home=self.test_root,
        )

        content = provider.prepare_user_message_content(
            "Describe the video",
            attachment_refs=[self.video_ref.to_dict()],
        )

        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "video_url")
        self.assertTrue(content[1]["video_url"]["url"].startswith("data:video/mp4;base64,"))
