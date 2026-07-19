import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bot


class _FixedDateTime:
    @staticmethod
    def now(tz):
        return SimpleNamespace(timestamp=lambda: 2 * 3600)


class _Drive:
    def files(self):
        return self

    def get_media(self, fileId):
        return object()


class _TelegramBot:
    def __init__(self):
        self.photos_sent = 0

    async def send_video(self, **kwargs):
        raise TimeoutError("video timeout")

    async def send_photo(self, **kwargs):
        self.photos_sent += 1
        return SimpleNamespace(message_id=1)


class _Application:
    def create_task(self, coroutine):
        coroutine.close()


class PublicarMuestraTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_photo_when_video_upload_times_out(self):
        telegram_bot = _TelegramBot()
        context = SimpleNamespace(
            bot=telegram_bot,
            bot_data={"fotos_folders": [{"id": "photos"}], "videos_folders": [{"id": "videos"}]},
            application=_Application(),
        )

        async def choose_media(pool, media_type, loop, slot):
            return {
                "id": media_type,
                "mimeType": "video/mp4" if media_type == "video" else "image/jpeg",
            }

        with (
            patch.object(bot, "datetime", _FixedDateTime),
            patch.object(bot, "_obtener_media_horaria", side_effect=choose_media),
            patch.object(bot, "_get_drive_service", return_value=_Drive()),
            patch.object(bot, "_execute_drive", return_value=b"media"),
        ):
            await bot.publicar_muestra(context)

        self.assertEqual(telegram_bot.photos_sent, 1)


if __name__ == "__main__":
    unittest.main()
