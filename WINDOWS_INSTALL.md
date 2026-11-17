# GTNH Client Updater — instalación para Windows / Prism Launcher

## 1. Requisitos

- Windows 10/11, GTNH instalado en **Prism Launcher**.
- Minecraft cerrado antes de instalar o restaurar mods.
- El administrador debe facilitarte una URL HTTPS de `client-manifest.json` y el ejecutable `GTNHClientUpdater.exe` compilado en GitHub Actions.
- No se necesita Python, Java adicional ni Packwiz para ejecutar el `.exe`.

## 2. Primera instalación (interfaz gráfica)

1. Descomprime `GTNHClientUpdater-Windows.zip` en una carpeta estable, por ejemplo `C:\GTNHUpdater\`.
2. Abre `GTNHClientUpdater.exe`.
3. Pega la URL pública del `client-manifest.json` que te facilitó el administrador.
4. En Prism: clic derecho a la instancia GTNH > **Carpeta / Folder**; entra en la carpeta de juego que contiene `mods`, `config` y `options.txt`. Copia esa ruta al actualizador o usa **Examinar**.
5. Pulsa **Guardar ajustes** y **Comprobar actualizaciones**.
6. Examina cada línea: archivo anterior, versión aprobada y acción (`REEMPLAZAR`, `INSTALAR`, `ADOPTAR` o `IGUAL`). **Verifica especialmente los mods originales del modpack**.
7. Cierra Minecraft y pulsa **Instalar cambios**. Confirma la versión base de GTNH. Se guardarán respaldos antes de cambiar archivos.
8. Si cambia GT-Not-Leisure o Twist Space Technology, el programa puede pedir una segunda aprobación para respaldar y retirar **solo** los configs documentados, que el mod regenerará durante el siguiente inicio. Los ajustes personales de esos archivos no se fusionan automáticamente.
9. Abre GTNH y prueba que puedes conectarte al servidor.

**Importante:** el actualizador administra solo los addons seleccionados por el administrador. No actualizará automáticamente los cientos de mods originales de GTNH ni borrará `options.txt`, mundos, shaders o configuraciones ajenas al plan.

## 3. Activar actualizaciones automáticas desde Prism (opcional)

Después de la primera instalación y de comprobar que funciona, en el actualizador marca **Activar actualizaciones automáticas antes de iniciar Prism** y pulsa **Guardar ajustes**.

En Prism Launcher: **Editar instancia > Ajustes > Comandos personalizados > Pre-launch command**, introduce la ruta real al ejecutable, por ejemplo:

```
"C:\GTNHUpdater\GTNHClientUpdater.exe" --prelaunch
```

Prism suministra `INST_MC_DIR` a ese comando, así el actualizador detecta automáticamente la carpeta .minecraft de la instancia que arrancas. La primera vez prueba esta integración con una copia de la instancia. Documento oficial de Prism: https://prismlauncher.org/wiki/help-pages/custom-commands/.

En lanzamientos posteriores, el actualizador instalará cambios **solo de mods ya aceptados**. Si hay un addon nuevo, cambio de versión base GTNH, o regeneración de configs GTNL/TST pendiente de autorizar, bloqueará el arranque y pedirá que abras la GUI. Si no hay cambios, Prism continuará normalmente.

## 4. Restaurar

Cierra Minecraft, abre la GUI y pulsa **Restaurar última actualización**. Solo se restaura la última operación si los archivos conservan los hashes esperados; de lo contrario se bloquea para no sobrescribir cambios personales.

Los backups quedan en:

```
<carpeta de juego>\.gtnh-addon-updater\backups\
```

Los ajustes del programa quedan en `%LOCALAPPDATA%\GTNHClientUpdater\settings.json`.

Si GTNL/TST ya regeneraron los configs **después del arranque**, el rollback automático puede bloquearse porque el archivo cambió. No lo fuerces: conserva una copia de los configs nuevos y recupera manualmente los anteriores desde el backup o consulta al administrador.

## 5. Problemas habituales

- **El JAR ya existe / varios candidatos**: revisa en `mods/` duplicados y versiones antiguas. No borres archivos sin identificarlos.
- **SHA-256 incorrecto**: puede haber cambiado la descarga o el manifiesto. No omitas la comprobación; informa al administrador.
- **Manifest remoto no disponible**: verifica Internet y que la URL pública abra el JSON en navegador.
- **Version GTNH no coincide**: tu instalación base debe corresponder a la del servidor. No aceptes una versión por intuición.
- **SmartScreen o antivirus**: el `.exe` carece de firma comercial. Pide al administrador confirmar que procede de su propia compilación en GitHub Actions; no ejecutes binarios desconocidos.

El proyecto se ha probado con instalaciones simuladas en Linux, no con un servidor o una instancia Windows reales. Haced una primera prueba en una copia de la instancia antes de desplegarlo a todos los jugadores.
