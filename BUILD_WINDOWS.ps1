$ErrorActionPreference = 'Stop'
# Ejecutar desde la raiz de este proyecto en un Windows con Python 3.12 instalado.
py -3.12 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install -r client\requirements-build.txt
.\.venv-win\Scripts\python.exe -m unittest discover -s tests -p 'test_client_*.py' -v
.\.venv-win\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name GTNHClientUpdater --paths client client\main.py
Write-Host 'Ejecutable disponible en dist\GTNHClientUpdater.exe'
