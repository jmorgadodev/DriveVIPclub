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
        self.photo_media = []
        self.photo_reply_markups = []
        self.deleted_message_ids = []

    async def send_video(self, **kwargs):
        raise TimeoutError("video timeout")

    async def send_photo(self, **kwargs):
        self.photos_sent += 1
        self.photo_chat_ids.append(kwargs["chat_id"])
        self.photo_media.append(kwargs["photo"])
        self.photo_reply_markups.append(kwargs.get("reply_markup"))
        return SimpleNamespace(
            message_id=self.photos_sent,
            photo=[SimpleNamespace(file_id="telegram-photo-id")],
        )

    async def delete_message(self, **kwargs):
        self.deleted_message_ids.append(kwargs["message_id"])


class _Application:
    def create_task(self, coroutine):
        coroutine.close()


class _Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _SalesValues:
    def __init__(self):
        self.batch_body = None

    def get(self, **kwargs):
        return _Request({"values": [[
            "user_id", "username", "email", "plan", "fecha_inicio",
            "fecha_fin", "estado", "fecha_registro", "origen", "notas",
            "payment_ids",
        ]]})

    def batchUpdate(self, **kwargs):
        self.batch_body = kwargs["body"]
        return _Request({})


class _SalesService:
    def __init__(self):
        self.values_api = _SalesValues()

    def spreadsheets(self):
        return self

    def values(self):
        return self.values_api


class GoogleRequestTests(unittest.TestCase):
    def test_sheet_requests_force_cycle_collection(self):
        request = SimpleNamespace(execute=lambda: {"ok": True})
        with patch.object(bot.gc, "collect") as collect:
            result = bot._execute_sheets(request)

        self.assertEqual(result, {"ok": True})
        collect.assert_called_once_with()

    def test_drive_requests_force_cycle_collection(self):
        request = SimpleNamespace(execute=lambda: b"media")
        with patch.object(bot.gc, "collect") as collect:
            result = bot._execute_drive(request)

        self.assertEqual(result, b"media")
        collect.assert_called_once_with()


class SalesSheetTests(unittest.TestCase):
    def test_approved_payment_creates_a_complete_bot_sale(self):
        service = _SalesService()
        with patch.object(bot, "_get_sheets_service", return_value=service):
            result = bot._procesar_pago_sheet_sync(
                123,
                "payment-1",
                "semanal",
                "2026-07-20",
                username="buyer",
            )

        self.assertEqual(result["status"], "processed")
        self.assertFalse(result["renewal"])
        self.assertTrue(result["needs_email"])
        data = service.values_api.batch_body["data"]
        self.assertEqual(data[0]["range"], "'Hoja 1'!A2:E2")
        self.assertEqual(
            data[0]["values"][0],
            ["123", "buyer", "", "semanal", "2026-07-20"],
        )
        self.assertEqual(data[1]["range"], "'Hoja 1'!H2:K2")
        self.assertEqual(data[1]["values"][0][1:], ["bot", "", "payment-1"])


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

        self.assertEqual(telegram_bot.photos_sent, 2)
        self.assertEqual(
            telegram_bot.photo_chat_ids,
            [bot.PUBLIC_GROUP_ID, bot.CHANNEL_ID],
        )
        self.assertEqual(telegram_bot.photo_media[1], "telegram-photo-id")
        self.assertEqual(
            telegram_bot.photo_reply_markups,
            [bot.SALES_MENU, bot.SALES_MENU],
        )
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


class PrivateMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_text_receives_sales_menu(self):
        user = SimpleNamespace(id=123, username="buyer")
        message = SimpleNamespace(text="hola", reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_user=user,
            effective_chat=SimpleNamespace(type="private"),
            message=message,
        )

        with patch.object(bot, "registrar_evento", new=AsyncMock()) as track:
            await bot.manejar_mensaje(update, SimpleNamespace())

        track.assert_awaited_once_with(user, "private_message", "free_text")
        message.reply_text.assert_awaited_once_with(
            "¿Qué te gustaría revisar? Elige una opción para continuar:",
            reply_markup=bot.SALES_MENU,
        )


if __name__ == "__main__":
    unittest.main()
