// ============================================
// CancelarSuscripciones — Apps Script
// Pegar en Extensiones > Apps Script de la
// planilla Drivetelegram (1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs)
// ============================================

var SHEET_ID = '1jFaDduB_uEKOavf0ZgRrw9zuodyKwHGVDdlLSsH_cVs';
var HOJA = 'Hoja 1';
var DRIVE_FOLDER_ID = '1HxAlgzaZ9acatHGGsVcqcFZXj5Q225vf';
var DRIVE_FOLDER_NAME = 'HDD';

function obtenerCarpeta() {
  // Primero intentar por ID
  try {
    return DriveApp.getFolderById(DRIVE_FOLDER_ID);
  } catch (e) {
    Logger.log('No se encontró por ID, buscando por nombre...');
  }
  // Buscar por nombre en todo el Drive
  var carpetas = DriveApp.getFoldersByName(DRIVE_FOLDER_NAME);
  while (carpetas.hasNext()) {
    var f = carpetas.next();
    Logger.log('Encontrada por nombre: ' + f.getName() + ' - ID: ' + f.getId());
    return f;
  }
  // Buscar en Mi Unidad
  var raiz = DriveApp.getRootFolder();
  var hijos = raiz.getFolders();
  while (hijos.hasNext()) {
    var h = hijos.next();
    if (h.getName().toUpperCase().indexOf('HDD') >= 0) {
      Logger.log('Encontrada en Mi Unidad: ' + h.getName() + ' - ID: ' + h.getId());
      return h;
    }
  }
  Logger.log('ERROR: No se encontró ninguna carpeta con nombre relacionado a HDD');
  return null;
}

function revisarVencidos() {
  var sheet = SpreadsheetApp.openById(SHEET_ID).getSheetByName(HOJA);
  var folder = obtenerCarpeta();
  if (!folder) return;
  var datos = sheet.getDataRange().getValues();
  for (var i = 1; i < datos.length; i++) {
    var fila = datos[i];
    var email = fila[2];
    var estado = fila[6];
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
// 🧪 TEST: ejecuta esto PRIMERO
// ============================================
function testear() {
  Logger.log('Correo actual: ' + Session.getActiveUser().getEmail());
  Logger.log('Sheet: ' + SpreadsheetApp.openById(SHEET_ID).getName());
  Logger.log('Drive: ' + DriveApp.getRootFolder().getName());
  Logger.log('---');
  var f = obtenerCarpeta();
  if (f) {
    Logger.log('Carpeta OK: ' + f.getName());
    Logger.log('Dueño: ' + f.getOwner().getEmail());
  }
}

// ============================================
// INSTALACIÓN:
// 1. Pegar código, guardar como "CancelarSuscripciones"
// 2. Elegir "testear" y EJECUTAR → aceptar permisos
// 3. Ver > Registros para ver resultado
// 4. Si funciona, elegir "instalarTrigger" y EJECUTAR
// ============================================

function instalarTrigger() {
  ScriptApp.newTrigger('revisarVencidos')
    .timeBased().everyDays(1).atHour(4).create();
}

function eliminarTrigger() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() == 'revisarVencidos') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
}
