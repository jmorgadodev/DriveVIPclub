import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
        self.photo_chat_ids = []
        self.deleted_message_ids = []

    async def send_video(self, **kwargs):
        raise TimeoutError("video timeout")

    async def send_photo(self, **kwargs):
        self.photos_sent += 1
        self.photo_chat_ids.append(kwargs["chat_id"])
        return SimpleNamespace(message_id=1)

    async def delete_message(self, **kwargs):
        self.deleted_message_ids.append(kwargs["message_id"])


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
        self.assertEqual(telegram_bot.photo_chat_ids, [bot.PUBLIC_GROUP_ID])
        self.assertEqual(context.bot_data["group_sample_ids"], {1})

    async def test_midnight_cleanup_deletes_group_samples(self):
        telegram_bot = _TelegramBot()
        context = SimpleNamespace(
            bot=telegram_bot,
            bot_data={"group_sample_ids": {10, 11}, "promo_message_ids": {10, 11, 12}},
        )

        await bot.limpiar_muestras_grupo(context)

        self.assertEqual(set(telegram_bot.deleted_message_ids), {10, 11})
        self.assertEqual(context.bot_data["group_sample_ids"], set())
        self.assertEqual(context.bot_data["promo_message_ids"], {12})


class OcultarSalidaTests(unittest.IsolatedAsyncioTestCase):
    async def test_deletes_public_group_departure_message(self):
        member = SimpleNamespace(id=99)
        message = SimpleNamespace(
            chat_id=bot.PUBLIC_GROUP_ID,
            left_chat_member=member,
            delete=AsyncMock(),
        )
        update = SimpleNamespace(effective_message=message)

        await bot.ocultar_salida(update, SimpleNamespace())

        message.delete.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
