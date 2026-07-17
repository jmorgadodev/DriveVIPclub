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
    var email = fila[2];        // C
    var estado = fila[6];       // G

    if (estado == 'vencido' && email && email.toString().indexOf('@') > -1) {
      revocarAcceso(folder, email.toString());
      sheet.getRange(i + 1, 7).setValue('acceso_revocado');
    }
  }
}

function revocarAcceso(folder, email) {
  var permisos = folder.getPermissions();
  for (var j = 0; j < permisos.length; j++) {
    var perm = permisos[j];
    if (perm.getEmail() == email) {
      perm.remove();
      return;
    }
  }
}

// ============================================
// INSTALACIÓN (seguir este orden exacto):
// ============================================
// 1. Pegar este código en el editor Apps Script
// 2. Guardar (Ctrl+S) con nombre "CancelarSuscripciones"
// 3. En el editor, seleccionar "autorizar" y
//    presionar EJECUTAR → aceptar permisos
//    (pide acceso a Sheets y Drive)
// 4. En el editor, seleccionar "instalarTrigger"
//    y presionar EJECUTAR
// 5. Listo — correrá automáticamente a las 4 AM
// ============================================

function autorizar() {
  // Solo para forzar pantalla de permisos
  revisarVencidos();
}

function instalarTrigger() {
  ScriptApp.newTrigger('revisarVencidos')
    .timeBased()
    .everyDays(1)
    .atHour(4)
    .create();
}

function eliminarTrigger() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() == 'revisarVencidos') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
}
