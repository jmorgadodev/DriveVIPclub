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
- Hoja 1 columns: A(user_id), B(username), C(email), D(plan), E(fecha_inicio), F(fecha_fin=formula), G(estado=formula), H(fecha_registro)
- Formulas: F=IF D=semanal→E+7, mensual→E+30; G=IF F<TODAY→"vencido" else "activo"
- Mensajes tab: key → text with {admin} and {user} placeholders

## Bot flow
1. /start → register user in Sheet → welcome + IMAGEN_BIENVENIDA
2. /semanal ($4.990) or /mensual ($8.990) → create MP preference → send payment link
3. Polling thread cada 30s consulta MP API `v1/payments/search?status=approved`
4. Payment detected → sets plan+fecha_inicio in Sheet, marks PENDING_GMAIL[user_id]
5. User sends email → bot shares Drive folder via API → saves email in Sheet
6. Bot checks daily at 04:00 AM for expired users → revokes Drive access
7. Self-ping every 10min to prevent Render spin-down
8. mensaje_automatico publica en horarios fijos: 00:00, 08:00, 12:00, 16:00, 20:00 Chile (~50% con IMAGEN_RECORDATORIO, borrar 4h)
9. nuevo_miembro welcomes new members in public group (con IMAGEN_BIENVENIDA, borrar 2h)
10. verificar_proximos_vencer a las 10:00 AM avisa usuarios que expiran mañana
11. Stats dinámicos se cargan desde el listado sheet al inicio y se refrescan 6:00/18:00

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
- `bienvenida.png` → enviada en /start y nuevo_miembro (se borra en 2h)
- `ventajas.png` → enviada en /ventajas
- `recordatorio.png` → enviada aleatoriamente en auto_08/12/16/20 (~50%, máx 1 cada 4h, se borra en 4h)
- `precios.png`, `transparencia.png` → para fijar manualmente en el grupo
- `demo_drive.png` → enviada al conceder acceso al Drive

## Canal @DriveVIPclub
- ID: -1004398583245
- Bot es admin, puede publicar
- Descripcion con link al grupo y bot, foto puesta
- Auto-posts programados: 9:00, 13:00, 18:00, 21:00 Chile
- CANAL_TEXTS rotan 5 variantes con stats y CTA al bot
- Primeros posts de bienvenida fijados en el canal
- publicar_muestra(): 1 media/hora desde Drive, auto-borrado 3600s
  - Carga imágenes (~1028) y videos ≤10MB (~101) vía API Drive (paginación 200), cachea en bot_data
  - 70% foto / 30% video aleatorio
  - Lleva set `used_images` para no repetir; cuando agota, reinicia
  - Descarga vía get_media() (sin almacenamiento intermedio) y envía como InputFile(BytesIO)
  - Usa send_photo() para imágenes, send_video() para videos

## Tools
- Test Drive: python test_drive.py (created per-test, removed after)
- Check Sheet: python check_sheet.py
