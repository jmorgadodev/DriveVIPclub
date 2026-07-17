// ============================================
// CancelarSuscripciones — Apps Script
// Pegar en Extensiones > Apps Script de la
// planilla Drivetelegram (1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs)
// ============================================

var SHEET_ID = '1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs';
var DRIVE_FOLDER_ID = '1HxAlgzaZ9acatHGGsVcqcFZXj5Q225vf';
var HOJA = 'Hoja 1';

function revisarVencidos() {
  var sheet = SpreadsheetApp.openById(SHEET_ID).getSheetByName(HOJA);
  var datos = sheet.getDataRange().getValues();
  var folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);

  for (var i = 1; i < datos.length; i++) {
    var fila = datos[i];
    var userId = fila[0];       // A
    var username = fila[1];     // B
    var email = fila[2];        // C
    var plan = fila[3];         // D
    var inicio = fila[4];       // E
    var fin = fila[5];          // F (fecha_fin — fórmula)
    var estado = fila[6];       // G (estado — fórmula)
    var fechaReg = fila[7];     // H

    // Solo si está vencido, tiene email, y no se ha revocado aún
    if (estado == 'vencido' && email && email.toString().indexOf('@') > -1) {
      revocarAcceso(folder, email.toString());
      sheet.getRange(i + 1, 7).setValue('acceso_revocado');
      Logger.log('Revocado: ' + email + ' (fila ' + (i + 1) + ')');
    }
  }
}

function revocarAcceso(folder, email) {
  var permisos = folder.getPermissions();
  for (var j = 0; j < permisos.length; j++) {
    var perm = permisos[j];
    if (perm.getEmail() == email) {
      perm.remove();
      Logger.log('Permiso removido para ' + email);
      return;
    }
  }
  Logger.log('No se encontró permiso para ' + email + ' (quizás ya no tenía acceso)');
}

// ============================================
// Instalar trigger manual:
// 1. Ejecuta una vez `instalarTrigger()` desde
//    el editor Apps Script
// 2. O crea trigger desde el reloj en UI:
//    Reloj > + Añadir trigger > revisarVencidos
//    > Time-driven > Day timer > 4:00-5:00 AM
// ============================================

function instalarTrigger() {
  ScriptApp.newTrigger('revisarVencidos')
    .timeBased()
    .everyDays(1)
    .atHour(4)
    .create();
  Logger.log('Trigger instalado: todos los días a las 4 AM');
}

function eliminarTrigger() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() == 'revisarVencidos') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
  Logger.log('Trigger eliminado');
}
