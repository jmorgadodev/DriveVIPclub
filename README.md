# DriveVIPclub Bot 🤖

Bot de Telegram para gestión de membresías VIP con acceso a Google Drive.

**Admin:** @backadminthree

---

## Arquitectura

```
Usuario de Telegram
    │
    ▼
Bot @DriveVIPclubBot
    │  • Bienvenida automática (se borra en 2h)
    │  • Mensajes promocionales cada 4h
    │  • Comandos: /start /precios /contenido /contacto
    │
    ▼
Google Sheets (Base de datos)
    │  • user_id, username, email, plan
    │  • fecha_inicio, fecha_fin, estado
    │
    ▼
Google AppScript (Automatización)
    │  • Cron cada 6h → verifica vencimientos
    │  • Elimina del grupo VIP a quienes vencieron
    │
    ▼
Railway + UptimeRobot (Hosting 24/7 gratuito)
```

---

## Estructura del proyecto

```
DriveVIPclub/
├── bot.py            ← Lógica principal del bot
├── config.py         ← Lee variables de entorno (.env)
├── mensajes.py       ← Textos que el bot envía
├── AppScript.js      ← Código para Google AppScript
├── requirements.txt  ← Dependencias Python
├── Procfile          ← Start command para Railway
├── .env.example      ← Plantilla de variables de entorno
└── .gitignore        ← Excluye .env y secrets del repo
```

---

## Instalación local

```bash
# 1. Clonar el repositorio
git clone https://github.com/TU_USUARIO/DriveVIPclub.git
cd DriveVIPclub

# 2. Crear entorno virtual
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate # Linux/Mac

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
copy .env.example .env
# Edita .env con tus valores reales

# 5. Ejecutar el bot
python bot.py
```

---

## Variables de entorno (`.env`)

| Variable | Descripción |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token de @BotFather |
| `PUBLIC_GROUP_ID` | ID del grupo público |
| `VIP_GROUP_ID` | ID del grupo VIP privado |
| `CHANNEL_ID` | ID del canal de anuncios |
| `ADMIN_USERNAME` | Usuario administrador (ej. @koooke) |
| `GOOGLE_SHEET_ID` | ID de la Google Sheet |
| `GOOGLE_SHEET_RANGE` | Rango de la hoja (default: `Hoja1!A:I`) |

---

## Planes y precios

| Plan | Precio | Duración |
|---|---|---|
| Semanal | $4.990 CLP | 7 días |
| Mensual | $7.990 CLP | 30 días |
| Bimestral | $9.990 CLP | 60 días |

---

## Despliegue en Railway

1. Crear cuenta en [railway.app](https://railway.app) (sin tarjeta)
2. **New Project** → Deploy from GitHub repo
3. Agregar las variables de entorno (mismas del `.env`)
4. El `Procfile` ya configura el start command automáticamente

---

## Google AppScript

1. Abre tu Google Sheet → **Extensiones → Apps Script**
2. Pega el contenido de `AppScript.js`
3. Ejecuta `crearTrigger()` **una sola vez**
4. El script verificará membresías cada 6 horas automáticamente

---

## Estructura Google Sheets

| Columna | Campo | Descripción |
|---|---|---|
| A | user_id | ID de Telegram |
| B | username | @usuario |
| C | email | Email para Google Drive |
| D | plan | semanal / mensual / bimestral |
| E | fecha_inicio | Fecha de pago |
| F | fecha_fin | Fecha de vencimiento |
| G | estado | activo / vencido / pendiente |
| H | fecha_registro | Fecha de unión al grupo |
| I | contacto_inicial | Primera interacción con el bot |

---

## Flujo completo

1. Usuario entra al grupo público
2. Bot lo registra en Sheets y envía bienvenida (se borra en 2h)
3. Usuario paga y el bot confirma el pago automáticamente
4. Usuario envía su Gmail y el bot comparte el acceso al Drive
5. El bot registra el plan, fechas y estado en Sheets
6. El bot revoca automáticamente los accesos vencidos
7. Bot envía mensajes promocionales cada 4h en el grupo público

---

## Seguridad

- ✅ Tokens y IDs en `.env` (nunca en el código)
- ✅ `.env` en `.gitignore` (nunca sube a GitHub)
- ✅ El bot detecta pagos aprobados automáticamente mediante MercadoPago
- ✅ `token.json` de Google también en `.gitignore`
