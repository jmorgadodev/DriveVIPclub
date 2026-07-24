# CONTEXT.md — Resumen Ejecutivo del Proyecto

> Generado: 2026-07-21  
> Proyecto: **DriveVIPclub** — Bot de Telegram + Google Sheets + Google Drive + MercadoPago  
> Chat original: Revisión de demo solicitada que no aparece en panel de Sheets

---

## Objetivo del Proyecto

Bot de Telegram que vende membresías VIP para acceder a un Google Drive con contenido exclusivo. El bot gestiona:
- Captación de leads en grupo público de Telegram
- Pagos automatizados via MercadoPago (CLP) y PayPal (USD)
- Activación de acceso a Google Drive
- Control de vencimientos y revocación automática de acceso
- Demos gratuitas de 10 minutos
- Publicación automática de muestras (6/día) con rotación
- Dashboard en Google Sheets con Ventas, Demos, Embudo, Estadísticas

---

## Estado Actual

### Lo que funciona
- Bot en producción en Render (srv-d9d019urnols73ciq81g, https://drivevipclub.onrender.com)
- Comandos: /start, /precios, /contenido, /contacto, /lista, /ventajas, /semanal, /mensual, /paypal, /demo, /testdrive
- Pagos MP: polling cada 30s, detección automática, registro en Ventas
- Pagos PayPal: polling cada 120s
- Drive compartido: se agrega/revoca acceso via API
- Muestras automáticas: 6 diarias en grupo público + canal, rotación 3 visibles
- Stats semanales: se actualizan cada lunes 06:00 desde listado público
- Bienvenida con imagen + autodestrucción 15 min via cola persistente (hoja Eliminaciones)
- Demo flow: /demo → pide Gmail → comparte DEMO_FOLDER_ID → timer 10 min → revoca

### Lo que se verificó en esta sesión
- **Google Sheets** tiene 8 pestañas: Dashboard, Estadisticas, Ventas, Mensajes, Embudo, Eliminaciones, PayPalOrders, Demos
- **Ventas:** solo header, sin datos (0 ventas concretadas)
- **Demos:** solo header, sin datos (0 demos completadas)
- **Embudo:** ~185 eventos registrados (entradas/salidas, bot_start, view_list, view_prices, demo_solicitada)
- Última demo solicitada: usuario **983495140** (sin username) a las **10:37:27** → registró `demo_solicitada` en Embudo pero **abortó** (nunca envió Gmail, salió del grupo 18s después)
- **Conclusión: la demo nunca se activó** porque el usuario no completó el paso del email

---

## Decisiones Técnicas y Arquitectura

### Stack
| Componente | Tecnología |
|------------|-----------|
| Bot | Python 3.12 + python-telegram-bot v20+ |
| Hosting | Render (free tier, se duerme sin actividad) |
| DB | Google Sheets (API v4) — sin SQL |
| Drive | Google Drive API v3 (service account + OAuth token) |
| Pagos CLP | MercadoPago API (polling cada 30s) |
| Pagos USD | PayPal API (polling cada 120s) |
| No webhooks | Render free tier rechaza POST entrantes |

### Arquitectura clave
- **Sin webhooks**: MP y PayPal se consultan por polling desde el bot
- **Health server**: raw socket en puerto 10000 para uptimerobot
- **Self-ping**: hilo separado que hace GET cada 600s para evitar spin-down
- **Google API**: thread pool `_GOOGLE_EXECUTOR` (max 1 worker) con locks (`_SHEETS_API_LOCK`, `_DRIVE_API_LOCK`)
- **Pagos MP**: thread pool `_PAYMENT_EXECUTOR` (max 1 worker)
- **Formato demo**: 10 min exactos, control en memoria (`DEMO_EXPIRY`) + persistencia en hoja Demos
- **Anti-abuso demos**: único por usuario, verificado contra hoja Demos

### Configuración crítica
- `GOOGLE_SHEET_ID` = `1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs`
- `LISTADO_SHEET_ID` = `1K5lJLdMJfPH76JrV4uC9-QdDly8rLg8XAWxoecWAe3k`
- `DRIVE_FOLDER_ID` = `1EHGYTF0QHiZUFq8FEaa3W3UppGGupaKb` (carpeta maestra)
- `DEMO_FOLDER_ID` = `1S9UnBT5hA17RIN8CThm14n7ED86r4dyC` (1611 creadores, 9219 fotos + 2539 videos de muestra)
- Service account: `impulsacv-gsc-mcp@gen-lang-client-0417969585.iam.gserviceaccount.com`
- Creds path: `C:\Users\jorge\.codex\credentials\gsc-service-account.json`
- Admin: `@backadminthree`

---

## Archivos Clave

| Archivo | Propósito |
|---------|-----------|
| `bot.py` (2439 líneas) | Bot principal — comandos, pagos, demos, muestras, jobs |
| `config.py` | Variables de entorno mapeadas desde .env |
| `mensajes.py` | Textos fallback para comandos si falla carga desde Sheets |
| `create_demo.py` | Script para poblar carpeta DEMO desde carpeta maestra (1 vez) |
| `check_sheet.py` | Script de depuración para leer Google Sheets |
| `AppScript.js` | Código Google Apps Script para verificar vencimientos cada 6h |
| `CancelarSuscripciones.gs` | Apps Script alternativo para revocar accesos vencidos |
| `AGENTS.md` | Memoria técnica del proyecto para asistentes IA |
| `render.yaml` | Config de despliegue en Render |
| `requirements.txt` | Dependencias Python |
| `.env` | Variables de entorno locales (no commiteado) |

### Estructura de sheets (Google Sheets ID: `1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs`)

**Ventas** — A(user_id), B(username), C(email), D(plan), E(fecha_inicio), F(fecha_fin=formula), G(estado=formula), H(fecha_registro), I(origen), J(notas), K(payment_ids)
- Fórmulas: F=SI D=semanal→E+7, mensual→E+30; G=SI F<HOY()→"vencido" si no "activo"
- Origen: `bot` (auto) o `manual` (admin)
- Límite: 5.000 filas

**Demos** — A(user_id), B(username), C(email), D(requested_at), E(expires_at), F(status)
- Status: `activo` → `expirado`

**Embudo** — A(timestamp), B(user_id), C(username), D(event), E(source)
- Eventos: group_join, group_leave, bot_start, view_list, view_prices, plan_selected, payment_link_created, payment_approved, demo_from_menu, demo_solicitada, demo_access_granted, etc.

---

## Tareas Pendientes / Próximos Pasos

### Inmediatos
1. **Investigación de mercado / promoción web** — el proyecto lleva 0 ventas concretadas (~185 eventos de embudo, 0 pagos). Probar estrategias de captación fuera de Telegram.
2. **Monitorear la demo flow** — identificar por qué los usuarios abandonan en el paso de enviar Gmail (el último usuario `983495140` abortó)

### Técnicos
3. Verificar que el bot está respondiendo correctamente en Render (logs)
4. Revisar que los mensajes desde Sheets se carguen (pestaña Mensajes parece vacía — solo header)
5. Considerar agregar reintentos en `_guardar_demo_sync` si falla la API
6. Probar `/testdrive` para verificar conectividad con Drive

---

## Errores Conocidos / Fragmentos Críticos

### Demo flow — punto donde se guarda en Sheets (solo tras recibir email)
```python
# bot.py:1332-1339
del PENDING_DEMO_GMAIL[user.id]
DEMO_EXPIRY[user.id] = datetime.now().timestamp() + 10 * 60
demo_emails = context.bot_data.setdefault('demo_emails', {})
demo_emails[user.id] = text
expires_str = (datetime.now() + timedelta(minutes=10)).isoformat(timespec='seconds')
await loop.run_in_executor(
    _GOOGLE_EXECUTOR, _guardar_demo_sync,
    user.id, user.username or 'sin_username', text, expires_str,
)
```

### Evento demo_solicitada se registra ANTES del email (línea 1300)
```python
await registrar_evento(user, 'demo_solicitada', 'demo')
PENDING_DEMO_GMAIL[uid] = True
```
Esto explica por qué aparece en Embudo pero no en hoja Demos — el usuario abortó antes de enviar email.

### _guardar_demo_sync (línea 362)
```python
def _guardar_demo_sync(user_id, username, email, expires_at):
    service = _get_sheets_service()
    _execute_sheets(service.spreadsheets().values().append(
        spreadsheetId=GOOGLE_SHEET_ID,
        range="'Demos'!A:F",
        valueInputOption='RAW',
        insertDataOption='INSERT_ROWS',
        body={'values': [[
            str(user_id), username, email,
            datetime.now(TZ).isoformat(timespec='seconds'),
            expires_at, 'activo',
        ]]},
    ))
```

### Health check raw socket (línea 2132)
```python
s.bind(('0.0.0.0', PORT))  # PORT = 10000
```

### Polling MP cada 30s (línea 2395)
```python
job_queue.run_repeating(_poll_payments, interval=30, first=5, name='poll_payments')
```

### Último evento registrado en Embudo
```
10:36:50 — 983495140 — bot_start — demo
10:37:23 — 983495140 — demo_from_menu — sample_button
10:37:27 — 983495140 — demo_solicitada — demo
10:37:45 — 983495140 — group_leave — public_group
```

---

## Comandos Útiles

```bash
# Ver estado del sheet
python check_sheet.py

# Verificar conectividad Drive (via bot Telegram: /testdrive)

# Logs de Render (vía dashboard o MCP render)
# Render service ID: srv-d9d019urnols73ciq81g
```
