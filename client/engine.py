"""Motor de sincronizacion seguro y sin dependencias graficas. Python 3.10+."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile

ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
DOMAIN_PATTERN = re.compile(r"[a-z0-9-]+\.github\.io\Z")
MAX_MANIFEST = 1024 * 1024
MAX_JAR = 300 * 1024 * 1024
STATE_SUBDIR = ".gtnh-addon-updater"
# Permitir solo rutas exactas validadas, nunca glob ni rutas arbitrarias del manifiesto.
GT_LANGUAGE = ("config/GregTech/GregTech.lang", "config/GregTech.lang")
ALLOWED_CONFIG_RESET = {
    "gtnl": ("config/GTNotLeisure/general.cfg", "config/GTNotLeisure/item.cfg",
             "config/GTNotLeisure/machine.cfg", "config/GTNotLeisure/recipe.cfg",
             "config/GTNotLeisure/re_avaritia.cfg", "config/GTNotLeisure/GTNotLeisure.cfg",
             "config/GTNotLeisure/main.cfg") + GT_LANGUAGE,
    "tst": ("config/TwistSpaceTechnology.cfg",) + GT_LANGUAGE,
}

class UpdateError(RuntimeError):
    pass


def sha256(file: Path) -> str:
    h = hashlib.sha256()
    with file.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _name(name: str) -> str:
    if not isinstance(name, str) or not name or name in (".", "..") or Path(name).name != name or "/" in name or "\\" in name or ":" in name:
        raise UpdateError(f"Nombre de archivo inseguro: {name!r}")
    if not name.lower().endswith(".jar"):
        raise UpdateError(f"No es JAR: {name}")
    return name


def _no_symlink(path: Path):
    if path.is_symlink():
        raise UpdateError(f"No se permiten enlaces simbolicos: {path}")


def _ordinary(path: Path):
    _no_symlink(path)
    if path.exists() and not path.is_file():
        raise UpdateError(f"Ruta ocupada por directorio u otro objeto: {path}")


def _url(url: str, repo: str | None = None, filename: str | None = None, manifest=False):
    if not isinstance(url, str):
        raise UpdateError("URL invalida")
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or p.username or p.password or p.port or not host or p.fragment:
        raise UpdateError(f"Solo se permite HTTPS sin credenciales: {url}")
    if manifest:
        if host not in ("raw.githubusercontent.com", "github.com") and not DOMAIN_PATTERN.fullmatch(host):
            raise UpdateError("Manifiesto: usar GitHub Pages, raw.githubusercontent.com o github.com")
    else:
        expect = f"/{repo}/releases/download/"
        if host != "github.com" or not repo or not p.path.startswith(expect):
            raise UpdateError(f"Asset fuera del repositorio oficial: {url}")
        if urllib.parse.unquote(p.path.rsplit("/", 1)[-1]) != filename:
            raise UpdateError("El nombre de descarga no coincide con el JAR aprobado")
    return url


def validate_manifest(data):
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise UpdateError("Version de esquema no soportada")
    if not ID_PATTERN.fullmatch(str(data.get("pack_id", ""))):
        raise UpdateError("pack_id invalido")
    if not isinstance(data.get("gtnh_version"), str) or not data["gtnh_version"].strip():
        raise UpdateError("Version GTNH ausente")
    mods = data.get("mods")
    if not isinstance(mods, list) or not mods or len(mods) > 100:
        raise UpdateError("Lista de mods vacia o excesiva")
    seen, names = set(), set()
    for m in mods:
        if not isinstance(m, dict) or not ID_PATTERN.fullmatch(str(m.get("id", ""))) or m["id"] in seen:
            raise UpdateError("ID de addon invalido/duplicado")
        seen.add(m["id"])
        if m.get("side") not in ("client", "both"):
            raise UpdateError(f"{m['id']}: no apto para clientes")
        if not isinstance(m.get("version"), str) or not m["version"] or not isinstance(m.get("name"), str):
            raise UpdateError("Version/nombre de mod invalidos")
        if not isinstance(m.get("repo"), str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", m["repo"]):
            raise UpdateError("Repo invalido")
        fn = _name(m.get("filename"))
        if fn.casefold() in names:
            raise UpdateError("JAR destino repetido")
        names.add(fn.casefold())
        if not isinstance(m.get("sha256"), str) or not HASH_PATTERN.fullmatch(m["sha256"]):
            raise UpdateError(f"SHA-256 invalido: {fn}")
        regex = m.get("installed_regex")
        if not isinstance(regex, str) or not regex.startswith(("^", "(?i)^")) or not regex.endswith("$") or len(regex) > 250:
            raise UpdateError("Regex de JAR no anclada")
        try:
            pattern = re.compile(regex)
        except re.error as e:
            raise UpdateError(f"Regex invalida: {e}") from e
        if not pattern.fullmatch(fn):
            raise UpdateError(f"Patron no reconoce el JAR deseado: {fn}")
        _url(m.get("url"), m["repo"], fn)
        resets = m.get("reset_on_update", [])
        if not isinstance(resets, list) or any(not isinstance(x, str) for x in resets):
            raise UpdateError("Lista de config resets invalida")
        if len(resets) != len(set(resets)) or not set(resets).issubset(ALLOWED_CONFIG_RESET.get(m["id"], ())):
            raise UpdateError(f"{m['id']}: ruta de config fuera de lista segura")
    return data


def fetch_manifest(url):
    _url(url, manifest=True)
    req = urllib.request.Request(url, headers={"User-Agent": "GTNH-Client-Updater/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            _url(r.geturl(), manifest=True)
            raw = r.read(MAX_MANIFEST + 1)
    except Exception as e:
        raise UpdateError(f"No se pudo consultar el manifiesto: {e}") from e
    if len(raw) > MAX_MANIFEST:
        raise UpdateError("Manifiesto demasiado grande")
    try:
        return validate_manifest(json.loads(raw.decode("utf-8-sig")))
    except (ValueError, UnicodeError) as e:
        raise UpdateError(f"JSON de manifiesto invalido: {e}") from e


def instance_dir(root):
    if not root:
        raise UpdateError("Seleccionar primero la carpeta .minecraft de Prism")
    p = Path(root).expanduser().resolve(strict=True)
    if not p.is_dir() or p.is_symlink():
        raise UpdateError("Instancia no es directorio normal")
    mods = p / "mods"
    if mods.is_symlink() or not mods.is_dir():
        raise UpdateError("La carpeta seleccionada debe contener mods/ ordinaria")
    if not (p / "config").is_dir() and not (p / "options.txt").is_file():
        raise UpdateError("No parece una carpeta de juego Minecraft (falta config/ y options.txt)")
    if (p / STATE_SUBDIR).is_symlink():
        raise UpdateError("Directorio interno no puede ser enlace simbolico")
    return p


def game_running(root):
    """Best-effort; siempre exigir que Minecraft este cerrado."""
    try:
        import psutil
    except ImportError:
        return []
    roottext = str(root).casefold()
    hits = []
    for proc in psutil.process_iter(["name", "cmdline", "cwd"]):
        try:
            name = (proc.info.get("name") or "").casefold()
            if name not in ("java.exe", "javaw.exe", "minecraft.exe", "java", "javaw"):
                continue
            cmd = " ".join(proc.info.get("cmdline") or []).casefold()
            cwd = (proc.info.get("cwd") or "").casefold()
            if roottext in cmd or cwd.startswith(roottext) or ("minecraft" in cmd and "launchwrapper" in cmd):
                hits.append(str(proc.pid))
        except (psutil.Error, OSError):
            continue
    return hits


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _read_json(path, default):
    _no_symlink(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _state(root):
    data = _read_json(root / STATE_SUBDIR / "state.json", {"managed": {}})
    if not isinstance(data, dict) or not isinstance(data.get("managed"), dict):
        raise UpdateError("Estado local corrupto")
    return data


@contextmanager
def _lock(root):
    folder = root / STATE_SUBDIR
    folder.mkdir(exist_ok=True)
    _no_symlink(folder)
    lock = folder / ".operation.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as e:
        raise UpdateError("Ya hay una operacion o quedo un bloqueo. Revisar el estado antes de borrar .operation.lock") from e
    try:
        os.write(fd, f"pid={os.getpid()}\n".encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _matches(mods_dir, pattern):
    regex = re.compile(pattern)
    result = []
    for f in mods_dir.iterdir():
        if f.is_symlink() and regex.fullmatch(f.name):
            raise UpdateError(f"JAR enlazado simbolicamente: {f.name}")
        if f.is_file() and regex.fullmatch(f.name):
            result.append(f.name)
    return sorted(result)


def _config_file(root, relative):
    if relative not in {x for values in ALLOWED_CONFIG_RESET.values() for x in values}:
        raise UpdateError("Ruta de configuracion no autorizada")
    path = root
    for component in Path(relative).parts:
        path = path / component
        _no_symlink(path)
    _ordinary(path)
    return path


def inspect(root, manifest):
    root = instance_dir(root)
    manifest = validate_manifest(manifest)
    state = _state(root)
    if state.get("pack_id") and state["pack_id"] != manifest["pack_id"]:
        raise UpdateError("La instancia esta vinculada a otro pack_id; no se mezclaran servidores")
    mods = root / "mods"
    changes, owners = [], {}
    for addon in manifest["mods"]:
        mod_id = addon["id"]
        destname = addon["filename"]
        desiredsha = addon["sha256"]
        candidates = _matches(mods, addon["installed_regex"])
        for name in candidates:
            if name.casefold() in owners:
                raise UpdateError(f"Patrones cruzados: {owners[name.casefold()]} y {mod_id} reconocen {name}")
            owners[name.casefold()] = mod_id
        known = state["managed"].get(mod_id)
        if known:
            name = _name(known.get("filename"))
            if name not in candidates or len(candidates) != 1:
                raise UpdateError(f"{mod_id}: falta JAR gestionado o hay duplicados: {candidates}")
            current = mods / name
            if sha256(current) != known.get("sha256"):
                raise UpdateError(f"{mod_id}: JAR modificado fuera del actualizador: {name}")
        else:
            if len(candidates) > 1:
                raise UpdateError(f"{mod_id}: varios JAR candidatos en Prism: {candidates}")
            name = candidates[0] if candidates else None
        dest = mods / destname
        _ordinary(dest)
        if dest.exists() and destname not in candidates:
            raise UpdateError(f"El archivo destino ya existe y no pertenece a {mod_id}: {destname}")
        if name == destname and sha256(dest) == desiredsha:
            action = "igual" if known else "adoptar"
        else:
            action = "reemplazar" if name else "instalar"
        resets = []
        if action == "reemplazar":
            for rel in addon.get("reset_on_update", []):
                path = _config_file(root, rel)
                if path.is_file():
                    resets.append({"path": rel, "sha256": sha256(path)})
        changes.append({"id": mod_id, "name": addon["name"], "version": addon["version"],
                        "config_resets": resets,
                        "action": action, "old_file": name, "new_file": destname, "sha256": desiredsha,
                        "url": addon["url"], "repo": addon["repo"]})
    return changes


def _download(mod, dest):
    _url(mod["url"], mod["repo"], mod["new_file"])
    req = urllib.request.Request(mod["url"], headers={"User-Agent": "GTNH-Client-Updater/0.1"})
    allowed = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(req, timeout=65) as r, tmp.open("wb") as output:
            final = urllib.parse.urlsplit(r.geturl())
            if final.scheme != "https" or (final.hostname or "").lower() not in allowed:
                raise UpdateError("Descarga redirigida a un servidor no autorizado")
            total = 0
            for chunk in iter(lambda: r.read(1024 * 1024), b""):
                total += len(chunk)
                if total > MAX_JAR:
                    raise UpdateError("JAR supera el limite de 300 MB")
                output.write(chunk)
        if sha256(tmp) != mod["sha256"]:
            raise UpdateError(f"SHA-256 incorrecto: {mod['new_file']}")
        if not zipfile.is_zipfile(tmp):
            raise UpdateError(f"Archivo no es un JAR valido: {mod['new_file']}")
        with zipfile.ZipFile(tmp) as jar:
            if not jar.namelist() or jar.testzip() is not None:
                raise UpdateError(f"JAR corrupto: {mod['new_file']}")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def _restore_transaction(root, folder, *, strict):
    tx = _read_json(folder / "transaction.json", None)
    if not tx:
        raise UpdateError("Backup incompleto")
    current = _state(root)
    if strict and current != tx["state_after"]:
        raise UpdateError("El estado cambio despues de esta actualizacion; no se sobrescribira")
    mods = root / "mods"
    for name, expected in tx["after_files"].items():
        target = mods / _name(name)
        _ordinary(target)
        if strict and ((target.is_file() and sha256(target)) or None) != expected:
            raise UpdateError(f"JAR cambiado desde la actualizacion: {name}")
    # Validar TODOS los backups antes de alterar ningun archivo.
    for name, expected in tx["before_files"].items():
        _ordinary(mods / _name(name))
        if expected is not None:
            saved = folder / "files" / name
            _ordinary(saved)
            if not saved.is_file() or sha256(saved) != expected:
                raise UpdateError(f"Backup corrupto: {name}")
    for rel, expected in tx.get("config_before", {}).items():
        target = _config_file(root, rel)
        if strict and ((sha256(target) if target.is_file() else None) != tx["config_after"][rel]):
            raise UpdateError(f"Config regenerado/cambiado; no se borrara durante rollback: {rel}")
        if expected is not None:
            saved = folder / "config" / rel
            _ordinary(saved)
            if not saved.is_file() or sha256(saved) != expected:
                raise UpdateError(f"Backup de config corrupto: {rel}")
    for name, expected in tx["before_files"].items():
        target = mods / name
        if expected is None:
            target.unlink(missing_ok=True)
        else:
            saved = folder / "files" / name
            tmp = target.with_name(".gtnh-restore-" + uuid.uuid4().hex + ".jar")
            try:
                shutil.copy2(saved, tmp)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
    for rel, expected in tx.get("config_before", {}).items():
        target = _config_file(root, rel)
        if expected is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            tempfile_path = target.with_name(".gtnh-config-" + uuid.uuid4().hex + ".tmp")
            try:
                shutil.copy2(folder / "config" / rel, tempfile_path)
                os.replace(tempfile_path, target)
            finally:
                tempfile_path.unlink(missing_ok=True)
    _write_json(root / STATE_SUBDIR / "state.json", tx["state_before"])
    tx["status"] = "rolled_back"
    _write_json(folder / "transaction.json", tx)


def apply(root, manifest, *, confirm_closed=False, allow_first_install=False,
          approve_config_reset=False, expected_changes=None, downloader=None, progress=None):
    root = instance_dir(root)
    manifest = validate_manifest(manifest)
    if not confirm_closed:
        raise UpdateError("Confirmar que Minecraft esta cerrado")
    if game_running(root):
        raise UpdateError("Minecraft parece estar en ejecucion. Cerrarlo antes de continuar")
    report = lambda msg: progress(msg) if progress else None
    with _lock(root):
        folder = root / STATE_SUBDIR
        if (folder / "pending.json").exists():
            raise UpdateError("Hay una transaccion interrumpida; restaurar antes de actualizar")
        changes = inspect(root, manifest)
        if expected_changes is not None and changes != expected_changes:
            raise UpdateError("El plan cambio desde la revision visible. Volver a comprobar")
        work = [v for v in changes if v["action"] != "igual"]
        if not work:
            return None
        if any(v["config_resets"] for v in work) and not approve_config_reset:
            raise UpdateError("Config GTNL/TST requiere revision y aprobacion en la interfaz")
        if not allow_first_install and any(v["action"] in ("adoptar", "instalar") for v in work):
            raise UpdateError("Hay nuevos addons o adopciones; revisar primero desde la interfaz grafica")
        if game_running(root):
            raise UpdateError("Minecraft inicio durante la comprobacion; abortado")
        txn_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        staging = folder / "staging" / txn_id
        staging.mkdir(parents=True, exist_ok=False)
        downloads = {}
        try:
            for mod in work:
                if mod["action"] in ("instalar", "reemplazar"):
                    report(f"Descargando {mod['new_file']}...")
                    staged = staging / mod["new_file"]
                    (downloader or _download)(mod, staged)
                    _ordinary(staged)
                    if not staged.is_file() or sha256(staged) != mod["sha256"] or not zipfile.is_zipfile(staged):
                        raise UpdateError(f"Descarga no verificada: {mod['new_file']}")
                    downloads[mod["id"]] = staged
            if game_running(root):
                raise UpdateError("Minecraft inicio durante las descargas; no se modifico mods/")
            if inspect(root, manifest) != changes:
                raise UpdateError("La carpeta mods/ cambio desde la comprobacion; volver a comprobar")
            before_state = _state(root)
            after_state = json.loads(json.dumps(before_state))
            after_state["pack_id"] = manifest["pack_id"]
            touched = sorted(set(n for x in work for n in (x["old_file"], x["new_file"]) if n))
            before_files = {n: (sha256(root / "mods" / n) if (root / "mods" / n).is_file() else None) for n in touched}
            config_paths = sorted({v["path"] for change in work for v in change["config_resets"]})
            config_before = {rel: sha256(_config_file(root, rel)) for rel in config_paths}
            config_after = {rel: None for rel in config_paths}
            after_files = dict(before_files)
            for mod in work:
                old, new = mod["old_file"], mod["new_file"]
                after_files[new] = mod["sha256"]
                if old and old != new:
                    after_files[old] = None
                after_state["managed"][mod["id"]] = {"filename": new, "sha256": mod["sha256"], "version": mod["version"]}
            backup = folder / "backups" / txn_id
            (backup / "files").mkdir(parents=True)
            for name, h in before_files.items():
                if h:
                    shutil.copy2(root / "mods" / name, backup / "files" / name)
                    if sha256(backup / "files" / name) != h:
                        raise UpdateError(f"Backup no verificado: {name}")
            for rel, h in config_before.items():
                target = backup / "config" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(_config_file(root, rel), target)
                if sha256(target) != h:
                    raise UpdateError(f"Backup config no verificado: {rel}")
            tx = {"config_before": config_before, "config_after": config_after,
                  "id": txn_id, "status": "pending", "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "before_files": before_files, "after_files": after_files,
                  "state_before": before_state, "state_after": after_state}
            _write_json(backup / "transaction.json", tx)
            _write_json(folder / "pending.json", {"transaction": txn_id})
            try:
                for mod in work:
                    old, new = mod["old_file"], mod["new_file"]
                    if mod["action"] in ("instalar", "reemplazar"):
                        tmp = root / "mods" / (".gtnh-write-" + uuid.uuid4().hex + ".jar")
                        try:
                            shutil.copy2(downloads[mod["id"]], tmp)
                            if sha256(tmp) != mod["sha256"]:
                                raise UpdateError("Fallo de copia")
                            os.replace(tmp, root / "mods" / new)
                        finally:
                            tmp.unlink(missing_ok=True)
                        if old and old != new:
                            (root / "mods" / old).unlink()
                    report(f"Aplicado: {mod['id']} -> {new}")
                for rel in config_paths:
                    _config_file(root, rel).unlink()
                    report(f"Config GTNL/TST respaldado para regeneracion: {rel}")
                _write_json(folder / "state.json", after_state)
                tx["status"] = "applied"
                _write_json(backup / "transaction.json", tx)
                (folder / "pending.json").unlink(missing_ok=True)
            except Exception:
                report("Fallo al aplicar; restaurando archivos del backup...")
                _restore_transaction(root, backup, strict=False)
                (folder / "pending.json").unlink(missing_ok=True)
                raise
            report(f"Actualizacion correcta. Backup: {txn_id}")
            return txn_id
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def rollback(root, transaction_id=None):
    root = instance_dir(root)
    if game_running(root):
        raise UpdateError("Cerrar Minecraft antes de restaurar")
    with _lock(root):
        folder = root / STATE_SUBDIR
        pending = _read_json(folder / "pending.json", None)
        if pending:
            selected = pending["transaction"]
            strict = False
        else:
            backups = sorted((folder / "backups").glob("*/transaction.json"), reverse=True) if (folder / "backups").exists() else []
            choices = [p.parent for p in backups if _read_json(p, {}).get("status") == "applied"]
            if not choices:
                raise UpdateError("No hay actualizaciones que restaurar")
            selected = choices[0].name
            strict = True
        if transaction_id and selected != transaction_id:
            raise UpdateError("Solo es posible restaurar la ultima transaccion aplicada")
        backup = folder / "backups" / selected
        _restore_transaction(root, backup, strict=strict)
        (folder / "pending.json").unlink(missing_ok=True)
        return selected
