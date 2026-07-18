## Config / Env
- Token: 8767323818:AAGyw7g6jNrJWqj3_5p2MAVBM1ejfJNIJ4Y
- Sheets: GOOGLE_SHEET_ID=1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs
- Service account: impulsacv-gsc-mcp@gen-lang-client-0417969585.iam.gserviceaccount.com
- Service account creds path: C:\Users\jorge\.codex\credentials\gsc-service-account.json
- MP Access Token: APP_USR-8269931565190295-071710-0fe418d9bc33e5f5d4ed78badd7a9bd8-3547846805
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
1. /start → register user in Sheet → welcome
2. /semanal ($4.990) or /mensual ($8.990) → create MP preference → send payment link
3. Polling thread cada 30s consulta MP API `v1/payments/search?status=approved`
4. Payment detected → sets plan+fecha_inicio in Sheet, marks PENDING_GMAIL[user_id]
5. User sends email → bot shares Drive folder via API → saves email in Sheet
6. Bot checks daily at 04:00 AM for expired users → revokes Drive access
7. Self-ping every 10min to prevent Render spin-down
8. mensaje_automatico publica en horarios fijos: 00:00, 08:00, 12:00, 16:00, 20:00 Chile
9. nuevo_miembro welcomes new members in public group

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

## Tools
- Test Drive: python test_drive.py (created per-test, removed after)
- Check Sheet: python check_sheet.py
