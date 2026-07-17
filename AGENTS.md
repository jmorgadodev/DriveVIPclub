## Config / Env
- Token: 8767323818:AAGyw7g6jNrJWqj3_5p2MAVBM1ejfJNIJ4Y
- Sheets: GOOGLE_SHEET_ID=1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs
- Service account: impulsacv-gsc-mcp@gen-lang-client-0417969585.iam.gserviceaccount.com
- Service account creds path: C:\Users\jorge\.codex\credentials\gsc-service-account.json
- MP Access Token: APP_USR-8269931565190295-071710-0fe418d9bc33e5f5d4ed78badd7a9bd8-3547846805
- Drive folder ID: 1HxAlgzaZ9acatHGGsVcqcFZXj5Q225vf (carpeta "HDD", dueño vpack2034@gmail.com)
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
3. MP sends webhook to https://drivevipclub.onrender.com/webhook
4. Bot processes payment: sets plan+fecha_inicio in Sheet, marks PENDING_GMAIL[user_id]
5. User sends email → bot shares Drive folder via API → saves email in Sheet
6. Bot checks daily at 04:00 AM for expired users → revokes Drive access
7. Self-ping every 10min to prevent Render spin-down
8. mensaje_automatico rotates auto_4h/auto_noche/auto_finde every 4h in public group
9. nuevo_miembro welcomes new members in public group
10. Offline 22-08 (only affects mensajes_automaticos)

## Tools
- Test Drive: python test_drive.py (created per-test, removed after)
- Check Sheet: python check_sheet.py
