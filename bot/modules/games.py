"""Módulos de juego: trivia, piedra-papel-tijera y dados.

Todos sin IA: usan datos locales y azar.
Cada juego mantiene su estado en `StateStore` (por chat).
"""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import GameSession, Message, OutgoingMessage
from ..core.state import StateStore

# ----------------------------------------------------------------------
# TRIVIA
# ----------------------------------------------------------------------
TRIVIA_QUESTIONS: List[Dict[str, Any]] = [
    {
        "q": "¿Cuál es el planeta más grande del sistema solar?",
        "a": "Júpiter",
        "options": ["Tierra", "Júpiter", "Saturno", "Marte"],
    },
    {
        "q": "¿En qué año llegaron los humanos a la Luna?",
        "a": "1969",
        "options": ["1959", "1969", "1979", "1961"],
    },
    {
        "q": "¿Cuál es el símbolo químico del oro?",
        "a": "Au",
        "options": ["Ag", "Au", "Gd", "Go"],
    },
    {
        "q": "¿Quién escribió 'Cien años de soledad'?",
        "a": "Gabriel García Márquez",
        "options": ["Mario Vargas Llosa", "Gabriel García Márquez", "Pablo Neruda", "Jorge Luis Borges"],
    },
    {
        "q": "¿Cuántos lados tiene un hexágono?",
        "a": "6",
        "options": ["5", "6", "7", "8"],
    },
    {
        "q": "¿Cuál es el océano más grande?",
        "a": "El Pacífico",
        "options": ["El Atlántico", "El Índico", "El Ártico", "El Pacífico"],
    },
    {
        "q": "¿Qué instrumento mide la temperatura?",
        "a": "El termómetro",
        "options": ["El barómetro", "El termómetro", "El higrómetro", "El altímetro"],
    },
    {
        "q": "¿En qué país está la muralla china?",
        "a": "China",
        "options": ["Japón", "China", "Mongolia", "India"],
    },
    {
        "q": "¿Cuál es el segundo planeta del sistema solar?",
        "a": "Venus",
        "options": ["Mercurio", "Venus", "Marte", "Júpiter"],
    },
    {
        "q": "¿Cuántos minutos tiene una hora?",
        "a": "60",
        "options": ["30", "60", "90", "100"],
    },
    {
        "q": "¿Quién desarrolló la teoría de la relatividad?",
        "a": "Albert Einstein",
        "options": ["Isaac Newton", "Albert Einstein", "Niels Bohr", "Max Planck"],
    },
    {
        "q": "¿Cuál es el número pi?",
        "a": "3.14159...",
        "options": ["2.71828...", "3.14159...", "1.41421...", "9.86960..."],
    },
    {
        "q": "¿Qué ojo se cierra al parpadear?",
        "a": "Los dos",
        "options": ["El izquierdo", "El derecho", "Los dos", "Ninguno"],
    },
    {
        "q": "¿Cuál es el mamífero más grande?",
        "a": "La ballena azul",
        "options": ["El elefante", "La ballena Azul", "El hipopótamo", "El rinoceronte"],
    },
    {
        "q": "¿En qué ciudad se firmó la Declaración de los Derechos Humanos?",
        "a": "En Paris, 1948",
        "options": ["En Paris, 1948", "En Londres, 1945", "En Nueva York, 1776", "En Roma, 1947"],
    },
]


# ----------------------------------------------------------------------
# TRIVIA: banco de preguntas por carpeta de texto
# ----------------------------------------------------------------------
# Carpeta: data/trivia/*.txt
# Formato de cada linea:  pregunta | respuesta | opcion1;opcion2;opcion3;opcion4
# Ejemplo:  ¿Capital de Francia? | París | Madrid;Londres;París;Berlín
# Si la carpeta esta vacia, se usan las preguntas embebidas (TRIVIA_QUESTIONS).
TRIVIA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "trivia"


def _parse_trivia_line(line: str) -> Optional[Dict[str, Any]]:
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        return None
    pregunta = parts[0]
    respuesta = parts[1]
    opciones = [o.strip() for o in parts[2].split(";") if o.strip()]
    if not pregunta or not respuesta or len(opciones) < 2:
        return None
    if respuesta not in opciones:
        opciones = [respuesta] + [o for o in opciones if o != respuesta]
    return {"q": pregunta, "a": respuesta, "options": opciones}


def load_trivia_questions(folder: Path | str = TRIVIA_DIR) -> List[Dict[str, Any]]:
    """Carga preguntas de todos los .txt de la carpeta (elige al azar entre
    archivos y entre preguntas). Si la carpeta no tiene archivos, usa el
    banco embebido."""
    folder = Path(folder)
    items: List[Dict[str, Any]] = []
    if folder.exists():
        archivos = sorted(folder.glob("*.txt"))
        for archivo in archivos:
            try:
                for line in archivo.read_text(encoding="utf-8").splitlines():
                    parsed = _parse_trivia_line(line)
                    if parsed:
                        items.append(parsed)
            except OSError:
                continue
    if items:
        return items
    return list(TRIVIA_QUESTIONS)


def trivia_start(state: StateStore, chat_id: int, rounds: int = 5,
                 thread_id: Optional[int] = None) -> OutgoingMessage:
    banco = load_trivia_questions()
    preguntas = random.sample(banco, min(rounds, len(banco)))
    session = state.get_session(chat_id, thread_id)
    session.game = "trivia"
    session.active = True
    session.score = 0
    session.data = {
        "preguntas": preguntas,
        "indice": 0,
        "aciertos": 0,
        "total": len(preguntas),
    }
    state.save_session(session, thread_id)
    return trivia_next_question(state, chat_id)


def trivia_next_question(state: StateStore, chat_id: int,
                           thread_id: Optional[int] = None) -> OutgoingMessage:
    session = state.get_session(chat_id, thread_id)
    data = session.data
    idx = int(data.get("indice", 0))
    preguntas = data.get("preguntas", [])
    if idx >= len(preguntas):
        total = int(data.get("total", 0)) or 1
        aciertos = int(data.get("aciertos", 0))
        session.active = False
        session.score = aciertos
        state.save_session(session, thread_id)
        pct = round(aciertos * 100 / total)
        return OutgoingMessage(
            text=f"🏁 *Trivia terminada*\nAciertos: *{aciertos}/{total}* ({pct}%)\n"
            f"¡Otra vez con `/trivia`!"
        )

    pregunta = preguntas[idx]
    opciones = pregunta.get("options", [])
    letras = ["A", "B", "C", "D", "E"]
    lineas = [f"🧠 *Trivia {idx + 1}/{len(preguntas)}*", "", pregunta["q"], ""]
    for i, op in enumerate(opciones):
        lineas.append(f"{letras[i]}) {op}")
    lineas.append("")
    lineas.append("Respondé con la letra o el texto. /salir para terminar.")

    session.data = dict(data)
    session.data["indice"] = idx
    session.data["actual"] = pregunta
    session.active = True
    state.save_session(session, thread_id)
    return OutgoingMessage(text="\n".join(lineas))


def trivia_answer(state: StateStore, chat_id: int, text: str,
                  thread_id: Optional[int] = None) -> Optional[OutgoingMessage]:
    session = state.get_session(chat_id, thread_id)
    if not session.active or session.game != "trivia":
        return None
    data = dict(session.data)
    pregunta = data.get("actual")
    if not pregunta:
        return trivia_next_question(state, chat_id)

    respuesta = (text or "").strip().lower()
    letras = ["a", "b", "c", "d", "e"]
    correcta_idx = 0
    for i, op in enumerate(pregunta.get("options", [])):
        if op.lower() == str(pregunta.get("a", "")).lower():
            correcta_idx = i
            break
    correcta = str(pregunta.get("a", "")).lower()
    opcion = ""
    if len(respuesta) == 1 and respuesta in letras:
        opcion = pregunta["options"][letras.index(respuesta)].lower()
    else:
        opcion = respuesta

    acierto = opcion == correcta
    if acierto:
        data["aciertos"] = int(data.get("aciertos", 0)) + 1
        session.score = int(data["aciertos"])
        texto = f"✅ ¡Correcto! *{pregunta['a']}*"
    else:
        texto = f"❌ Incorrecto. La respuesta era *{pregunta['a']}*"

    data["indice"] = int(data.get("indice", 0)) + 1
    session.data = data
    state.save_session(session, thread_id)

    siguiente = trivia_next_question(state, chat_id)
    return OutgoingMessage(text=f"{texto}\n\n{siguiente.text}")


# ----------------------------------------------------------------------
# PIEDRA PAPEL TIJERA
# ----------------------------------------------------------------------
RPS_OPCIONES = {
    "piedra": "piedra",
    "rock": "piedra",
    "r": "piedra",
    "papel": "papel",
    "paper": "papel",
    "t": "papel",
    "tijera": "tijera",
    "tijeras": "tijera",
    "scissors": "tijera",
    "s": "tijera",
}


def rps_play(text: str) -> OutgoingMessage:
    eleccion = RPS_OPCIONES.get((text or "").strip().lower())
    if not eleccion:
        return OutgoingMessage(
            text="👊 Elegí *piedra*, *papel* o *tijera* (ej: `piedra`)."
        )
    bot = random.choice(["piedra", "papel", "tijera"])
    gana = {"piedra": "tijera", "papel": "piedra", "tijera": "papel"}
    if eleccion == bot:
        resultado = "🤝 Empate"
    elif gana[eleccion] == bot:
        resultado = "🎉 ¡Ganaste!"
    else:
        resultado = "💀 Perdiste"
    return OutgoingMessage(
        text=f"{resultado}\nVos: *{eleccion}*\nYo: *{bot}*"
    )


# ----------------------------------------------------------------------
# DADOS
# ----------------------------------------------------------------------
def dice_roll(argumentos: List[str]) -> OutgoingMessage:
    dados = 1
    caras = 6
    if argumentos:
        try:
            dados = int(argumentos[0])
        except ValueError:
            dados = 1
    if len(argumentos) > 1:
        try:
            caras = int(argumentos[1])
        except ValueError:
            caras = 6
    dados = max(1, min(10, dados))
    caras = max(2, min(100, caras))
    resultados = [random.randint(1, caras) for _ in range(dados)]
    total = sum(resultados)
    texto = f"🎲 {dados} dado(s) de {caras} caras: {', '.join(str(r) for r in resultados)}"
    if dados > 1:
        texto += f"\nTotal: *{total}*"
    return OutgoingMessage(text=texto)


__all__ = [
    "TRIVIA_QUESTIONS",
    "trivia_start",
    "load_trivia_questions",
    "trivia_next_question",
    "trivia_answer",
    "rps_play",
    "dice_roll",
]
