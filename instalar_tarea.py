"""
agente_futbol/instalar_tarea.py
Configura el Task Scheduler de Windows para ejecutar el agente
automáticamente a la 01:00 AM hora Lima cada día.

Ejecutar UNA SOLA VEZ como administrador:
  python instalar_tarea.py
"""

import subprocess
import sys
import os

# Ruta al intérprete de Python actual
PYTHON_EXE = sys.executable

# Ruta al script principal del agente
AGENTE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agente.py")

# Nombre de la tarea en el programador
NOMBRE_TAREA = "AgenteAnalisisFutbol"

# Hora de ejecución (01:00 AM hora Lima = UTC-5, equivale a 06:00 UTC)
HORA_EJECUCION = "01:00"


def instalar():
    print(f"Configurando tarea programada: {NOMBRE_TAREA}")
    print(f"  Python: {PYTHON_EXE}")
    print(f"  Script: {AGENTE_SCRIPT}")
    print(f"  Hora:   {HORA_EJECUCION} (hora local de la PC, asegúrate que esté en UTC-5/Lima)")
    print()

    # Eliminar si ya existe
    subprocess.run(
        ["schtasks", "/Delete", "/TN", NOMBRE_TAREA, "/F"],
        capture_output=True
    )

    # Crear la tarea con schtasks
    comando = [
        "schtasks", "/Create",
        "/TN", NOMBRE_TAREA,
        "/TR", f'"{PYTHON_EXE}" "{AGENTE_SCRIPT}"',
        "/SC", "DAILY",
        "/ST", HORA_EJECUCION,
        "/RL", "HIGHEST",       # Ejecutar con privilegios máximos
        "/F",                   # Forzar creación si ya existe
    ]

    resultado = subprocess.run(comando, capture_output=True, text=True)

    if resultado.returncode == 0:
        print("✅ Tarea programada instalada correctamente.")
        print(f"   El agente correrá automáticamente cada día a las {HORA_EJECUCION}.")
        print()
        print("Para verificar: abre 'Programador de tareas' de Windows")
        print(f"y busca la tarea '{NOMBRE_TAREA}'.")
    else:
        print("❌ Error al instalar la tarea:")
        print(resultado.stderr)
        print()
        print("Intenta ejecutar este script como Administrador.")
        print("(Clic derecho sobre CMD → 'Ejecutar como administrador')")


def desinstalar():
    resultado = subprocess.run(
        ["schtasks", "/Delete", "/TN", NOMBRE_TAREA, "/F"],
        capture_output=True, text=True
    )
    if resultado.returncode == 0:
        print(f"✅ Tarea '{NOMBRE_TAREA}' eliminada.")
    else:
        print(f"❌ Error: {resultado.stderr}")


if __name__ == "__main__":
    if "--desinstalar" in sys.argv:
        desinstalar()
    else:
        instalar()
