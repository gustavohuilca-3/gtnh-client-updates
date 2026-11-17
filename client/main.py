"""GTNH Client Updater: interfaz Tkinter para Windows / Prism Launcher."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import engine
import settings

APP_TITLE = "GTNH Client Updater"


def run_prelaunch():
    """Modo opcional: solo actualiza addons ya aceptados en la GUI."""
    cfg = settings.load()
    if not cfg.get("automatic_prelaunch"):
        raise engine.UpdateError("Actualizacion automatica no habilitada en la interfaz")
    url = cfg.get("manifest_url", "")
    directory = os.environ.get("INST_MC_DIR") or cfg.get("instance_dir")
    if not directory:
        raise engine.UpdateError("Prism no proporciono INST_MC_DIR")
    manifest = engine.fetch_manifest(url)
    if cfg.get("approved_gtnh_version") != manifest["gtnh_version"]:
        raise engine.UpdateError("Cambio de version base GTNH: abrir GUI y revisar antes de jugar")
    changed = engine.inspect(directory, manifest)
    if any(c["config_resets"] for c in changed):
        raise engine.UpdateError("GTNL/TST requiere regeneracion local: abrir GUI y aprobar el backup de configs")
    if any(c["action"] in ("instalar", "adoptar") for c in changed):
        raise engine.UpdateError("Nuevos mods de cliente: abrir la interfaz grafica para autorizarlos")
    engine.apply(directory, manifest, confirm_closed=True, allow_first_install=False)
    return 0


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.minsize(810, 610)
        self.root.geometry("990x680")
        self.events = queue.Queue()
        self.manifest = None
        self.changes = None
        self.busy = False
        cfg = settings.load()
        self.url = tk.StringVar(value=cfg.get("manifest_url", ""))
        self.directory = tk.StringVar(value=os.environ.get("INST_MC_DIR") or cfg.get("instance_dir", ""))
        self.prelaunch = tk.BooleanVar(value=bool(cfg.get("automatic_prelaunch", False)))
        self.approved_base = cfg.get("approved_gtnh_version", "")
        self.checked_url = None
        self.checked_directory = None
        self.status = tk.StringVar(value="Configura el manifiesto y la carpeta .minecraft de Prism.")
        self._build()
        self.root.after(100, self._poll)

    def _build(self):
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Header.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Sub.TLabel", foreground="#4b5563")
        main = ttk.Frame(self.root, padding=18)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="GTNH Client Updater", style="Header.TLabel").pack(anchor="w")
        ttk.Label(main, text="Sincroniza solo los addons aprobados de tu servidor. No modifica las preferencias de Minecraft.", style="Sub.TLabel").pack(anchor="w", pady=(3, 12))

        source = ttk.LabelFrame(main, text="Origen de actualizaciones", padding=10)
        source.pack(fill="x", pady=(0, 10))
        ttk.Label(source, text="URL publica del client-manifest.json (GitHub Pages / GitHub raw):").pack(anchor="w")
        ttk.Entry(source, textvariable=self.url).pack(fill="x", pady=(4, 8))
        ttk.Label(source, text="Carpeta .minecraft de tu instancia Prism Launcher:").pack(anchor="w")
        row = ttk.Frame(source)
        row.pack(fill="x", pady=(4, 0))
        ttk.Entry(row, textvariable=self.directory).pack(side="left", fill="x", expand=True)
        self.browse = ttk.Button(row, text="Examinar...", command=self._browse)
        self.browse.pack(side="left", padx=(8, 0))
        self.savebtn = ttk.Button(row, text="Guardar ajustes", command=self._save)
        self.savebtn.pack(side="left", padx=(8, 0))

        results = ttk.LabelFrame(main, text="Addons", padding=9)
        results.pack(fill="both", expand=True, pady=(0, 10))
        self.table = ttk.Treeview(results, columns=("addon", "installed", "wanted", "action"), show="headings", height=9)
        columns = {"addon": ("Addon", 220), "installed": ("Archivo instalado", 250), "wanted": ("Versión aprobada", 140), "action": ("Acción", 135)}
        for key, (label, width) in columns.items():
            self.table.heading(key, text=label)
            self.table.column(key, width=width, stretch=(key == "installed"))
        scrollbar = ttk.Scrollbar(results, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ttk.Checkbutton(main, text="Actualizar automáticamente antes de iniciar Minecraft desde Prism (solo mods aceptados)", variable=self.prelaunch,
                        command=self._save).pack(anchor="w", pady=(0, 7))
        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=(0, 8))
        self.checkbutton = ttk.Button(buttons, text="Comprobar actualizaciones", command=self.check)
        self.checkbutton.pack(side="left")
        self.applybutton = ttk.Button(buttons, text="Instalar cambios", command=self.install, state="disabled")
        self.applybutton.pack(side="left", padx=7)
        self.rollbackbutton = ttk.Button(buttons, text="Restaurar ultima actualizacion", command=self.rollback)
        self.rollbackbutton.pack(side="left")
        self.progress = ttk.Progressbar(main, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 5))
        ttk.Label(main, textvariable=self.status).pack(anchor="w", pady=(0, 6))
        logs = ttk.LabelFrame(main, text="Actividad", padding=6)
        logs.pack(fill="both", expand=True)
        self.log = tk.Text(logs, height=6, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True)

    def _browse(self):
        chosen = filedialog.askdirectory(title="Seleccionar .minecraft de Prism Launcher")
        if chosen:
            self.directory.set(chosen)
            self.manifest = None
            self.changes = None
            self.applybutton.configure(state="disabled")

    def _save(self):
        try:
            data = {"manifest_url": self.url.get().strip(), "instance_dir": self.directory.get().strip(),
                    "automatic_prelaunch": self.prelaunch.get(), "approved_gtnh_version": self.approved_base}
            settings.save(data)
            self._append("Ajustes guardados. La publicacion de nuevas versiones sigue bajo control del administrador.")
        except Exception as ex:
            messagebox.showerror("Error", str(ex))

    def _append(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, value):
        self.busy = value
        status = "disabled" if value else "normal"
        for button in (self.checkbutton, self.rollbackbutton, self.savebtn, self.browse):
            button.configure(state=status)
        self.applybutton.configure(state="disabled" if value or not self.changes else "normal")
        if value:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _run(self, target, done):
        if self.busy:
            return
        self._set_busy(True)
        def worker():
            try:
                result = target()
                self.events.put(("done", done, result))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                msg = self.events.get_nowait()
                if msg[0] == "progress":
                    self._append(msg[1])
                elif msg[0] == "done":
                    self._set_busy(False)
                    msg[1](msg[2])
                elif msg[0] == "error":
                    self._set_busy(False)
                    self.status.set("Operacion interrumpida: consulta el mensaje y el estado de los backups.")
                    self._append("ERROR: " + msg[1])
                    messagebox.showerror("GTNH Client Updater", msg[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def check(self):
        url, directory = self.url.get().strip(), self.directory.get().strip()
        self._save()
        self.changes = None
        self.manifest = None
        self.applybutton.configure(state="disabled")
        self.status.set("Comprobando manifiesto y archivos locales...")
        def job():
            manifest = engine.fetch_manifest(url)
            changes = engine.inspect(directory, manifest)
            return manifest, changes
        def done(result):
            self.manifest, self.changes = result
            self.checked_url, self.checked_directory = url, directory
            for row in self.table.get_children():
                self.table.delete(row)
            for row in self.changes:
                self.table.insert("", "end", values=(row["id"], row["old_file"] or "No instalado", row["version"], row["action"].upper()))
            count = sum(c["action"] != "igual" for c in self.changes)
            self.status.set(f"GTNH {self.manifest['gtnh_version']} | revision {self.manifest.get('revision','?')} | {count} acciones pendientes")
            self.applybutton.configure(state="normal" if count else "disabled")
            self._append(f"Comprobacion correcta: {len(self.changes)} addons, {count} cambios.")
        self._run(job, done)

    def install(self):
        if not self.manifest or not self.changes:
            return
        if self.url.get().strip() != self.checked_url or self.directory.get().strip() != self.checked_directory:
            messagebox.showwarning("Volver a comprobar", "Cambiaste la URL o la carpeta de Prism. Ejecuta primero Comprobar actualizaciones.")
            return
        manifest = self.manifest
        changed = [m for m in self.changes if m["action"] != "igual"]
        if not changed:
            return
        if self.approved_base != manifest["gtnh_version"]:
            if not messagebox.askyesno("Confirmar version base",
                  f"El administrador declara GTNH {manifest['gtnh_version']}. ¿Has comprobado que tu instancia Prism usa la misma edicion de GTNH?"):
                return
            self.approved_base = manifest["gtnh_version"]
        details = "\n".join(f"{m['id']}: {m['old_file'] or '[nuevo]'} -> {m['new_file']}" for m in changed)
        if not messagebox.askyesno("Confirmar instalacion", f"Cierra Minecraft antes de continuar.\n\nSe modificarán SOLO estos JAR:\n\n{details[:1800]}\n\n¿Aplicar con backup?"):
            return
        configs = sorted({v["path"] for mod in changed for v in mod["config_resets"]})
        if configs:
            if not messagebox.askyesno("Regenerar configs GTNL / TST",
                  "Estas configuraciones locales se respaldaran y retiraran para regenerar en el siguiente arranque:\n\n"
                  + "\n".join(configs)[:1800] + "\n\nLos valores personales NO se fusionan automaticamente. ¿Autorizar?"):
                return
        self._save()
        directory = self.directory.get().strip()
        self.status.set("Descargando y verificando...")
        def job():
            return engine.apply(directory, manifest, confirm_closed=True, allow_first_install=True,
                                approve_config_reset=bool(configs), expected_changes=self.changes,
                                progress=lambda text: self.events.put(("progress", text)))
        def done(tx):
            self._append(f"Operacion finalizada. Backup: {tx or 'sin cambios'}")
            self.status.set("Actualizacion completada. Ya puedes iniciar Minecraft.")
            self.changes = None
            self.manifest = None
            self.applybutton.configure(state="disabled")
            messagebox.showinfo("Listo", "Addons actualizados. Las configuraciones personales no se modificaron.")
        self._run(job, done)

    def rollback(self):
        if not messagebox.askyesno("Restaurar", "¿Has cerrado Minecraft? Se restaurarán los JAR de la última actualización gestionada."):
            return
        directory = self.directory.get().strip()
        def job():
            return engine.rollback(directory)
        def done(tx):
            self.status.set("Restaurados los JAR anteriores. Comprueba Minecraft antes de jugar.")
            self._append("Restaurado backup " + tx)
            self.manifest = None
            self.changes = None
            self.applybutton.configure(state="disabled")
            messagebox.showinfo("Restauracion completa", "JAR restaurados; configuraciones personales intactas.")
        self._run(job, done)


def main(argv=None):
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--prelaunch", action="store_true", help="Comprobacion automatica previa a Prism")
    args = parser.parse_args(argv)
    if args.prelaunch:
        try:
            return run_prelaunch()
        except Exception as ex:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Actualizacion bloqueada", f"No se inicio Minecraft para evitar desincronizacion:\n\n{ex}\n\nAbre GTNH Client Updater para revisar.")
            root.destroy()
            return 2
    root = tk.Tk()
    try:
        App(root)
    except Exception as e:
        messagebox.showerror(APP_TITLE, str(e))
        return 2
    root.mainloop()
    return 0

if __name__ == "__main__":
    sys.exit(main())
