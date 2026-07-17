// ============================================================
// AppScript.js — Verificación automática de membresías
// DriveVIPclub — Google AppScript
// ============================================================
// Cómo instalar:
//   1. Abre tu Google Sheet → Extensiones → Apps Script
//   2. Pega este código completo
//   3. Ejecuta crearTrigger() UNA sola vez para activar el cron
//   4. Configura las constantes de la sección CONFIGURACIÓN
// ============================================================

// ---- CONFIGURACIÓN ----
const TELEGRAM_BOT_TOKEN = 'TU_TOKEN_AQUI';
const PUBLIC_GROUP_ID    = 'ID_GRUPO_PUBLICO';
const VIP_GROUP_ID       = 'ID_GRUPO_VIP';
const SHEET_ID           = 'ID_DE_TU_GOOGLE_SHEET';

// ---- FUNCIÓN PRINCIPAL (ejecuta cada 6 horas) ----

function verificarMembresias() {
  const sheet = SpreadsheetApp.openById(SHEET_ID).getActiveSheet();
  const data  = sheet.getDataRange().getValues();
  const hoy   = new Date();

  for (let i = 1; i < data.length; i++) {
    const row      = data[i];
    const userId   = row[0];   // col A
    const username = row[1];   // col B
    const plan     = row[3];   // col D
    const fechaFin = new Date(row[5]); // col F
    const estado   = row[6];   // col G

    // Si está activo y ya venció
    if (estado === 'activo' && hoy > fechaFin) {
      eliminarDeGrupo(VIP_GROUP_ID, userId);
      sheet.getRange(i + 1, 7).setValue('vencido'); // col G → "vencido"
      enviarMensaje(
        userId,
        `⚠️ Tu membresía *${plan}* ha vencido.\nContacta a @koooke para renovar 🔄`
      );
    }
  }
}

// ---- HELPERS ----

function eliminarDeGrupo(groupId, userId) {
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/kickChatMember`;
  UrlFetchApp.fetch(url, {
    method: 'post',
    payload: { chat_id: groupId, user_id: userId }
  });
}

function enviarMensaje(userId, texto) {
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
  UrlFetchApp.fetch(url, {
    method: 'post',
    payload: { chat_id: userId, text: texto, parse_mode: 'Markdown' }
  });
}

// ---- TRIGGER: ejecutar UNA sola vez ----
// Luego puedes eliminarlo — el trigger queda guardado en AppScript.

function crearTrigger() {
  // Evita duplicar triggers si ya existe uno
  const triggers = ScriptApp.getProjectTriggers();
  for (const t of triggers) {
    if (t.getHandlerFunction() === 'verificarMembresias') {
      Logger.log('⚠️ El trigger ya existe. No se creó uno nuevo.');
      return;
    }
  }

  ScriptApp.newTrigger('verificarMembresias')
    .timeBased()
    .everyHours(6)
    .create();

  Logger.log('✅ Trigger creado: verificarMembresias() cada 6 horas.');
}
