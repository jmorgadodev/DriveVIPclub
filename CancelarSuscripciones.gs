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
  var folder;
  try {
    folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);
  } catch (e) {
    Logger.log('Error accediendo a carpeta: ' + e.message);
    return;
  }
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
// 🧪 TEST: ejecuta esto PRIMERO para
//     verificar que el script funciona
// ============================================
function testear() {
  // 1. Verificar que podemos leer la planilla
  var sheet = SpreadsheetApp.openById(SHEET_ID);
  Logger.log('Sheet encontrada: ' + sheet.getName());
  
  // 2. Verificar acceso a Drive (fuerza permiso)
  var raiz = DriveApp.getRootFolder();
  Logger.log('Drive raiz: ' + raiz.getName());
  
  // 3. Verificar carpeta HDD
  try {
    var folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);
    Logger.log('Carpeta HDD encontrada: ' + folder.getName());
    Logger.log('Dueño: ' + folder.getOwner().getEmail());
  } catch (e) {
    Logger.log('ERROR: No se pudo acceder a la carpeta HDD');
    Logger.log('ID usado: ' + DRIVE_FOLDER_ID);
    Logger.log('Error: ' + e.message);
    // Buscar carpetas con nombre "HDD" como alternativa
    var carpetas = DriveApp.getFoldersByName('HDD');
    while (carpetas.hasNext()) {
      var alt = carpetas.next();
      Logger.log('Alternativa encontrada: ' + alt.getName() + ' - ID: ' + alt.getId());
    }
  }
}

// ============================================
// INSTALACIÓN (seguir este orden exacto):
// ============================================
// 1. Pegar este código en el editor Apps Script
// 2. Guardar (Ctrl+S) con nombre "CancelarSuscripciones"
// 3. En el editor, seleccionar "testear" y
//    presionar EJECUTAR → aceptar TODOS los permisos
// 4. Ve a VER > Registros para ver el resultado
// 5. Si testear funciona, selecciona "instalarTrigger"
//    y presiona EJECUTAR
// ============================================

function autorizar() {
  // Fuerza permisos de Sheets + Drive
  var sheet = SpreadsheetApp.openById(SHEET_ID);
  var raiz = DriveApp.getRootFolder();
  var folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);
  Logger.log('Autorización completa. Folder: ' + folder.getName());
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
