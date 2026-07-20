## Config / Env
- Token: (set via .env + Render dashboard)
- Sheets: GOOGLE_SHEET_ID=1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs
- Service account: impulsacv-gsc-mcp@gen-lang-client-0417969585.iam.gserviceaccount.com
- Service account creds path: C:\Users\jorge\.codex\credentials\gsc-service-account.json
- MP Access Token: (set via .env + Render dashboard)
- Drive folder ID: 1EHGYTF0QHiZUFq8FEaa3W3UppGGupaKb (carpeta maestra, dueño vpack2034@gmail.com)
- Render: srv-d9d019urnols73ciq81g, https://drivevipclub.onrender.com
- Admin: @backadminthree
- Public group: -1003902977064, VIP group: -1004328779223, Channel: -1004398583245
- MCPs: telegram, google-sheets, render, mercadopago (remote via opencode.jsonc)

## Sheet structure
- Hoja 1 es exclusiva para ventas: A(user_id), B(username), C(email), D(plan), E(fecha_inicio), F(fecha_fin=formula), G(estado=formula), H(fecha_registro), I(origen), J(notas), K(payment_ids)
- Formulas: F=IF D=semanal→E+7, mensual→E+30; G=IF F<TODAY→"vencido" else "activo"
- Origen: `bot` para pagos aprobados automáticamente y `manual` para ventas cargadas por el administrador
- El bot limita las lecturas de Hoja 1 a 5.000 ventas
- Mensajes tab: key → text with {admin} and {user} placeholders

## Bot flow
1. /start → registra evento en Embudo → welcome + IMAGEN_BIENVENIDA
2. /semanal ($4.990) or /mensual ($8.990) → create MP preference → send payment link
3. Polling thread cada 30s consulta MP API `v1/payments/search?status=approved`
4. Payment detected → crea/actualiza la venta en Hoja 1, marks PENDING_GMAIL[user_id]
5. User sends email → bot shares Drive folder via API → saves email in Sheet
6. Bot checks daily at 04:00 AM for expired users → revokes Drive access
7. Self-ping every 10min to prevent Render spin-down
8. El grupo recibe 6 muestras diarias: 10:05, 13:05, 16:05, 19:05, 22:05 y 23:30; se eliminan a medianoche
9. nuevo_miembro welcomes new members in public group (con IMAGEN_BIENVENIDA, borrar 15min)
10. verificar_proximos_vencer a las 10:00 AM avisa usuarios que expiran mañana
11. Stats se cargan desde `Estadisticas` al inicio; el listado completo se consulta solo los lunes a las 06:00 Chile y actualiza el mensaje fijado 478

## Listado Sheet (stats dinámicos)
- ID: `1K5lJLdMJfPH76JrV4uC9-QdDly8rLg8XAWxoecWAe3k`
- Columnas: A(Carpeta), B(Videos), C(Fotos), F(Tamaño Total)
- Placeholders: {carpetas}, {videos}, {fotos}, {tamano}
- Usados en: bienvenida, contenido, ventajas, auto_16

## Hooks
- Health: GET / → 200 OK (raw socket, no HTTP framework)
- Webhooks: NOT USED — Render free tier rejects POST; payment detection via MP API polling

## Apps Script
- `CancelarSuscripciones.gs`: pegar en Extensiones > Apps Script de la planilla Drivetelegram (`1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs`)
- Función `revisarVencidos()`: recorre Hoja 1, si estado=vencido y email tiene @, revoca permiso del folder HDD y marca "acceso_revocado"
- Ejecutar `instalarTrigger()` una vez para que corra automáticamente a las 4 AM

## Mensajes
- Los mensajes se cargan desde la pestaña Mensajes de la planilla principal
- Placeholders disponibles: {admin}, {user}
- Si falla la carga, usa fallback en mensajes.py

## Commands
- /lista → link a la planilla pública de contenido (`1K5lJLdMJfPH76JrV4uC9-QdDly8rLg8XAWxoecWAe3k`)
- /ventajas → texto ventajas + IMAGEN_VENTAJAS (ventajas.png)
- /start, /precios, /contenido, /contacto, /semanal, /mensual

## Images
- `bienvenida.png` → enviada en /start y nuevo_miembro (se borra en 15min)
- `ventajas.png` → enviada en /ventajas
- `recordatorio.png` → disponible para promociones manuales
- `precios.png`, `transparencia.png` → para fijar manualmente en el grupo
- `demo_drive.png` → enviada al conceder acceso al Drive

## Canal @DriveVIPclub
- ID: -1004398583245
- Bot es admin, puede publicar
- Descripcion con link al grupo y bot, foto puesta
- Auto-posts programados: 10:00, 15:00 y 20:00 Chile; borrar 3h
- CANAL_TEXTS rotan 5 variantes con stats y CTA al bot
- Primeros posts de bienvenida fijados en el canal

## Grupo público
- publicar_muestra(): 6 medias diarias en el grupo y canal
  - Carga imágenes y videos ≤20MB vía API Drive (paginación 200), cachea carpetas en bot_data
  - 70% foto / 30% video; videos de hasta 20MB
  - Selección determinista por bloque horario para evitar repeticiones consecutivas
  - Descarga y sube una vez al grupo; reutiliza el `file_id` de Telegram para el canal
  - Usa send_photo() para imágenes, send_video() para videos
  - Las muestras del grupo se borran a las 00:00; las del canal después de 3h
- Los mensajes de miembros que abandonan el grupo se eliminan automáticamente
- La pestaña `Embudo` registra ingresos, salidas, aperturas, planes, links de pago y pagos aprobados

## Tools
- Test Drive: python test_drive.py (created per-test, removed after)
- Check Sheet: python check_sheet.py
